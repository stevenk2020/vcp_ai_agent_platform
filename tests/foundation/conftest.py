"""Shared fixtures for the foundation acceptance tests.

Tests connect to the running stack (``make up``) as each database role. Passwords are read from
``secrets/<role>_password`` at the repository root; nothing secret is stored in this file.
"""

from __future__ import annotations

import os
import subprocess
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
}


def read_secret(name: str) -> str:
    path = SECRETS_DIR / name
    if not path.is_file():
        pytest.fail(f"missing secret file {path}; run `make secrets` first", pytrace=False)
    return path.read_text(encoding="utf-8").strip()


def connect(role: str, dbname: str | None = None) -> psycopg.Connection:
    """Open an autocommit connection as ``role`` (one of ROLE_SECRETS)."""
    return psycopg.connect(
        host=PG_HOST,
        port=PG_PORT,
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
