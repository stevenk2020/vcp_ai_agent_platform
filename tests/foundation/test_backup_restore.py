"""US4: encrypted backup, upload to the remote, and a restore that matches the live database.

Runs in the isolated "platform-test" stack (see conftest.backup_stack): throwaway age key, local
directory as the off-site remote, no real secrets.
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest

from conftest import REPO_ROOT

DUMP_RE = re.compile(r"^platform-\d{8}-\d{6}\.dump\.age$")
GLOBALS_RE = re.compile(r"^platform-\d{8}-\d{6}\.globals\.sql\.age$")


@pytest.fixture(scope="module")
def backed_up(backup_stack):
    """Seed some data, run one backup, and return the stack."""
    with backup_stack.connect("sync_writer") as conn:
        conn.execute("DROP TABLE IF EXISTS serving.restore_probe")
        conn.execute("CREATE TABLE serving.restore_probe (id int PRIMARY KEY, label text)")
        conn.execute("INSERT INTO serving.restore_probe SELECT g, 'row ' || g FROM generate_series(1, 250) g")
        # feature 004: the sync control tables travel in the same dump (a run row proves it)
        conn.execute((REPO_ROOT / "sync" / "sql" / "control.sql").read_text(encoding="utf-8"))
        conn.execute("DELETE FROM serving.sync_runs WHERE table_name = 'restore_probe'")
        conn.execute("INSERT INTO serving.sync_runs (table_name, trigger, started_at, outcome) "
                     "VALUES ('restore_probe', 'command', now(), 'succeeded')")
    result = backup_stack.compose("exec", "-T", "backup", "/usr/local/bin/backup.sh", check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return backup_stack


def _restore_test(stack, expect_ok: bool):
    env = {
        **stack.env,
        "AGE_IDENTITY_FILE": str(stack.identity),
        "COMPOSE_ARGS": " ".join(stack.args),
    }
    return subprocess.run(
        ["bash", "scripts/restore-test.sh"], cwd=REPO_ROOT, env=env, text=True, capture_output=True
    )


def test_backup_files_exist_locally_and_remotely_with_expected_names(backed_up):
    local = backed_up.compose("exec", "-T", "backup", "ls", "-1", "/var/lib/backup/dumps").stdout.split()
    assert any(DUMP_RE.match(name) for name in local), local
    assert any(GLOBALS_RE.match(name) for name in local), local

    remote = sorted(p.name for p in backed_up.remote.iterdir())
    assert any(DUMP_RE.match(name) for name in remote), remote
    assert any(GLOBALS_RE.match(name) for name in remote), remote


def test_backup_is_encrypted(backed_up):
    dump = next(p for p in backed_up.remote.iterdir() if DUMP_RE.match(p.name))
    assert dump.read_bytes().startswith(b"age-encryption.org/"), "dump is not an age file"
    assert b"PGDMP" not in dump.read_bytes()[:64], "dump looks like a plain pg_dump file"


def test_last_success_is_recorded_after_upload(backed_up):
    value = backed_up.compose("exec", "-T", "backup", "cat", "/var/lib/backup/last_success").stdout.strip()
    assert value.isdigit()


def test_daily_schedule_is_installed(backed_up):
    crontab = backed_up.compose("exec", "-T", "backup", "cat", "/etc/crontabs/root").stdout
    assert crontab.startswith("0 2 * * * /usr/local/bin/backup.sh"), crontab


def test_restore_reproduces_structure_and_row_counts(backed_up):
    result = _restore_test(backed_up, expect_ok=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "restore-test: OK" in result.stdout


def test_restore_test_detects_a_difference(backed_up):
    with backed_up.connect("sync_writer") as conn:
        conn.execute("INSERT INTO serving.restore_probe VALUES (9999, 'added after the backup')")
    try:
        result = _restore_test(backed_up, expect_ok=False)
        assert result.returncode != 0
        assert "MISMATCH" in result.stdout + result.stderr
    finally:
        with backed_up.connect("sync_writer") as conn:
            conn.execute("DELETE FROM serving.restore_probe WHERE id = 9999")


def test_restore_refuses_without_identity(backed_up):
    env = {**backed_up.env, "COMPOSE_ARGS": " ".join(backed_up.args)}
    env.pop("AGE_IDENTITY_FILE", None)
    result = subprocess.run(
        ["bash", "scripts/restore-test.sh"], cwd=REPO_ROOT, env=env, text=True, capture_output=True
    )
    assert result.returncode != 0
    assert "AGE_IDENTITY_FILE" in result.stderr


def test_backup_container_has_no_superuser_secret(backed_up):
    listing = backed_up.compose("exec", "-T", "backup", "ls", "-1", "/run/secrets").stdout.split()
    assert "postgres_superuser_password" not in listing
    assert sorted(listing) == ["backup_age_public_key", "backup_ro_password", "rclone_config"]
