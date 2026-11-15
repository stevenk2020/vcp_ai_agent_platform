"""US3: secrets stay out of the repository and out of services that do not need them."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT, SECRETS_DIR, compose

# service -> exact set of secrets it may receive (data-model.md "Secret" table).
# A service that appears in the config but not here fails the test: update the table deliberately.
EXPECTED_SECRETS = {
    "postgres": {"postgres_superuser_password"},
    "redis": set(),
    "caddy": set(),
    "db-init": {
        "postgres_superuser_password",
        "app_owner_password",
        "sync_writer_password",
        "agent_ro_password",
        "backup_ro_password",
        "api_rw_password",
    },
    "migrate": {"app_owner_password"},
    "backup": {"backup_ro_password", "backup_age_public_key", "rclone_config"},
    "api": {"api_rw_password", "auth_pepper", "api_external"},
    "api-test": {"api_rw_password", "auth_pepper", "api_external", "app_owner_password", "sync_writer_password"},
    "sync": {"sync_writer_password", "source_sqlserver"},
    "sync-test": {"sync_writer_password", "source_sqlserver", "app_owner_password", "agent_ro_password", "api_rw_password"},
}

COMPOSE_FILES = [REPO_ROOT / "compose.yaml", REPO_ROOT / "compose.backup.yaml"]


def _config() -> tuple[dict, str]:
    args = ["docker", "compose", "-f", "compose.yaml"]
    backup_ready = all((SECRETS_DIR / name).is_file() and (SECRETS_DIR / name).stat().st_size > 0
                       for name in ("backup_age_public_key", "rclone_config"))
    if (REPO_ROOT / "compose.backup.yaml").exists() and backup_ready:
        args += ["-f", "compose.backup.yaml"]
    args += ["--profile", "prod", "--profile", "test", "config", "--format", "json"]
    out = subprocess.run(args, cwd=REPO_ROOT, text=True, capture_output=True, check=True).stdout
    return json.loads(out), out


def _secret_values() -> list[str]:
    values = []
    for path in SECRETS_DIR.iterdir():
        if path.is_file() and path.name != "README.md":
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if path.name in ("rclone_config", "api_external"):
                    # ini-style: only values of secret-looking keys count (not [gdrive], type, scope)
                    key, _, value = line.partition("=")
                    if any(word in key.lower() for word in ("password", "secret", "token", "key")):
                        value = value.strip()
                        if len(value) >= 8:
                            values.append(value)
                elif len(line.strip()) >= 8:
                    values.append(line.strip())
    env = REPO_ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            key, _, value = line.partition("=")
            if any(word in key.lower() for word in ("password", "secret", "token", "key")):
                value = value.strip().strip("\"'")
                if len(value) >= 8:
                    values.append(value)
    return values


def test_each_service_receives_only_its_expected_secrets():
    config, _ = _config()
    for name, service in config["services"].items():
        assert name in EXPECTED_SECRETS, f"service {name} is not in EXPECTED_SECRETS: decide its secrets"
        received = {secret["source"] for secret in service.get("secrets", [])}
        assert received == EXPECTED_SECRETS[name], f"{name}: {received}"


def test_no_service_uses_env_file():
    for compose_file in COMPOSE_FILES:
        if compose_file.exists():
            text = compose_file.read_text(encoding="utf-8")
            assert not re.search(r"^\s*env_file\s*:", text, re.MULTILINE), compose_file.name


def test_no_secret_value_appears_in_resolved_compose_config():
    _, text = _config()
    for value in _secret_values():
        assert value not in text, "a secret value leaked into the resolved Compose config"


def test_docker_socket_is_never_mounted():
    config, _ = _config()
    for name, service in config["services"].items():
        for volume in service.get("volumes", []):
            assert "docker.sock" not in str(volume.get("source", "")), f"{name} mounts the Docker socket"


def test_only_caddy_and_loopback_postgres_publish_ports():
    config, _ = _config()
    for name, service in config["services"].items():
        for port in service.get("ports", []):
            if name == "caddy":
                assert str(port["published"]) in ("80", "443")
            elif name == "postgres":
                assert port.get("host_ip") == "127.0.0.1", port
            else:
                pytest.fail(f"{name} publishes a port: {port}")


def test_scan_secrets_is_clean():
    result = subprocess.run(
        ["bash", "scripts/scan-secrets.sh"], cwd=REPO_ROOT, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr


def test_scan_secrets_detects_a_planted_secret(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "secrets").mkdir(parents=True)
    (repo / "secrets" / "demo_password").write_text("s3cr3t-value-123456", encoding="utf-8")
    (repo / "notes.md").write_text("oops: s3cr3t-value-123456", encoding="utf-8")
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "notes.md"],
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"],
    ):
        subprocess.run(cmd, cwd=repo, check=True)

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "scan-secrets.sh")],
        cwd=repo,
        env={"SCAN_ROOT": str(repo), "PATH": "/usr/bin:/bin"},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert "notes.md" in result.stderr
    assert "s3cr3t-value-123456" not in result.stderr + result.stdout


def test_source_credentials_reach_only_the_sync_services():
    """Feature 004 (research R-10): the SQL Server credentials stay out of every other service."""
    config, _ = _config()
    holders = {name for name, service in config["services"].items()
               if "source_sqlserver" in {secret["source"] for secret in service.get("secrets", [])}}
    assert holders == {"sync", "sync-test"}


def test_no_source_credential_name_appears_in_any_environment_block():
    config, _ = _config()
    for name, service in config["services"].items():
        env = service.get("environment") or {}
        assert not [key for key in env if key.lower().startswith("sql_server")], name


def test_the_api_image_does_not_contain_the_sql_server_driver():
    result = compose("run", "--rm", "--no-deps", "--entrypoint", "python", "api", "-c", "import pymssql", check=False)
    assert result.returncode != 0 and "No module named" in result.stderr
