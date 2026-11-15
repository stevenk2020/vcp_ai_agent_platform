"""Shared fixtures for the foundation acceptance tests.

Tests connect to the running stack (``make up``) as each database role. Passwords are read from
``secrets/<role>_password`` at the repository root; nothing secret is stored in this file.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRETS_DIR = REPO_ROOT / "secrets"

PG_HOST = os.environ.get("POSTGRES_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
PG_DB = os.environ.get("POSTGRES_DB", "platform")

# role name -> secret file holding its password
ROLE_SECRETS = {
    "postgres": "postgres_superuser_password",
    "app_owner": "app_owner_password",
    "sync_writer": "sync_writer_password",
    "agent_ro": "agent_ro_password",
    "backup_ro": "backup_ro_password",
    "api_rw": "api_rw_password",
}


def read_secret(name: str) -> str:
    path = SECRETS_DIR / name
    if not path.is_file():
        pytest.fail(f"missing secret file {path}; run `make secrets` first", pytrace=False)
    return path.read_text(encoding="utf-8").strip()


def connect(role: str, dbname: str | None = None, port: int | None = None) -> psycopg.Connection:
    """Open an autocommit connection as ``role`` (one of ROLE_SECRETS)."""
    return psycopg.connect(
        host=PG_HOST,
        port=port or PG_PORT,
        dbname=dbname or PG_DB,
        user=role,
        password=read_secret(ROLE_SECRETS[role]),
        autocommit=True,
        connect_timeout=10,
    )


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run ``docker compose`` from the repository root."""
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=check,
    )


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def superuser():
    with connect("postgres") as conn:
        yield conn


@pytest.fixture
def app_owner():
    with connect("app_owner") as conn:
        yield conn


@pytest.fixture
def sync_writer():
    with connect("sync_writer") as conn:
        yield conn


@pytest.fixture
def agent_ro():
    with connect("agent_ro") as conn:
        yield conn


@pytest.fixture
def backup_ro():
    with connect("backup_ro") as conn:
        yield conn


@pytest.fixture
def api_rw():
    with connect("api_rw") as conn:
        yield conn


# ---- isolated stack for backup tests ---------------------------------------------------------------
# A second Compose project ("platform-test") with its own Postgres on port 55432, a throwaway age key,
# an empty rclone config and a local directory as the "remote". Real secrets and Google Drive are
# never touched, and the main stack keeps running.

BACKUP_PROJECT = "platform-test"
BACKUP_PORT = 55432


@dataclass
class BackupStack:
    tmp: Path
    env: dict[str, str]
    args: list[str]
    identity: Path
    remote: Path
    port: int = BACKUP_PORT
    extra: dict = field(default_factory=dict)

    def compose(self, *cmd: str, check: bool = True, input: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", "compose", *self.args, *cmd],
            cwd=REPO_ROOT,
            env=self.env,
            text=True,
            capture_output=True,
            check=check,
            input=input,
        )

    def connect(self, role: str) -> psycopg.Connection:
        return connect(role, port=self.port)


@pytest.fixture(scope="session")
def backup_stack(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("backup-stack")
    pubkey = tmp / "age_public_key"
    rclone_conf = tmp / "rclone.conf"
    remote = tmp / "remote"
    identity = tmp / "age_identity.txt"
    remote.mkdir()
    remote.chmod(0o777)
    pubkey.write_text("placeholder\n", encoding="utf-8")  # replaced below, needed for `compose build`
    rclone_conf.write_text("", encoding="utf-8")

    env = {
        **os.environ,
        "POSTGRES_PORT": str(BACKUP_PORT),
        "BACKUP_HEALTH_INTERVAL": "3s",
        "TEST_AGE_PUBKEY": str(pubkey),
        "TEST_RCLONE_CONFIG": str(rclone_conf),
        "TEST_REMOTE_DIR": str(remote),
    }
    args = [
        "-p", BACKUP_PROJECT,
        "-f", "compose.yaml",
        "-f", "compose.backup.yaml",
        "-f", "tests/foundation/compose.test.yaml",
    ]
    stack = BackupStack(tmp=tmp, env=env, args=args, identity=identity, remote=remote)

    stack.compose("build", "backup")
    image = f"{BACKUP_PROJECT}-backup"
    keygen = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "age-keygen", image],
        text=True, capture_output=True, check=True,
    )
    # stdout holds the identity file ("# created", "# public key", "AGE-SECRET-KEY-..."); stderr only
    # repeats the public key and would make the file unreadable for `age -i`
    identity.write_text(keygen.stdout, encoding="utf-8")
    public = re.search(r"# public key: (age1[0-9a-z]+)", keygen.stdout)
    assert public, "could not read the generated age public key"
    pubkey.write_text(public.group(1) + "\n", encoding="utf-8")

    try:
        stack.compose("up", "-d")
        subprocess.run(["bash", "scripts/wait-healthy.sh", *args], cwd=REPO_ROOT, env=env, check=True)
        yield stack
    finally:
        stack.compose("down", "-v", "--remove-orphans", check=False)
