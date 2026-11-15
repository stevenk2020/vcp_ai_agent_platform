"""US4: a missing or stale backup shows up as unhealthy (FR-013); a failed run keeps last_success."""

from __future__ import annotations

import json
import time

import pytest


def _set_last_success(stack, hours_ago: float) -> int:
    seconds = int(hours_ago * 3600)
    stack.compose(
        "exec", "-T", "backup", "bash", "-c",
        f"echo $(( $(date +%s) - {seconds} )) > /var/lib/backup/last_success",
    )
    return int(stack.compose("exec", "-T", "backup", "cat", "/var/lib/backup/last_success").stdout.strip())


def _health(stack) -> str:
    out = stack.compose("ps", "--all", "--format", "json", "backup").stdout
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    return rows[0].get("Health", "") if rows else ""


def _wait_for_health(stack, wanted: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        last = _health(stack)
        if last == wanted:
            return
        time.sleep(2)
    pytest.fail(f"backup health did not become {wanted!r} (last: {last!r})")


def test_stale_backup_is_unhealthy(backup_stack):
    _set_last_success(backup_stack, hours_ago=27)
    check = backup_stack.compose("exec", "-T", "backup", "/usr/local/bin/healthcheck.sh", check=False)
    assert check.returncode != 0
    _wait_for_health(backup_stack, "unhealthy")


def test_recent_backup_is_healthy(backup_stack):
    _set_last_success(backup_stack, hours_ago=1)
    check = backup_stack.compose("exec", "-T", "backup", "/usr/local/bin/healthcheck.sh", check=False)
    assert check.returncode == 0
    _wait_for_health(backup_stack, "healthy")


def test_missing_backup_after_grace_period_is_unhealthy(backup_stack):
    # no last_success and a first start longer than 26 hours ago
    backup_stack.compose(
        "exec", "-T", "backup", "bash", "-c",
        "rm -f /var/lib/backup/last_success && echo $(( $(date +%s) - 27*3600 )) > /var/lib/backup/started_at",
    )
    try:
        check = backup_stack.compose("exec", "-T", "backup", "/usr/local/bin/healthcheck.sh", check=False)
        assert check.returncode != 0
    finally:
        backup_stack.compose(
            "exec", "-T", "backup", "bash", "-c", "echo $(date +%s) > /var/lib/backup/started_at"
        )


def test_failed_backup_leaves_last_success_unchanged(backup_stack):
    before = _set_last_success(backup_stack, hours_ago=1)
    failed = backup_stack.compose(
        "exec", "-T", "-e", "BACKUP_REMOTE=nosuchremote:bucket",
        "backup", "/usr/local/bin/backup.sh", check=False,
    )
    assert failed.returncode != 0
    after = int(backup_stack.compose("exec", "-T", "backup", "cat", "/var/lib/backup/last_success").stdout.strip())
    assert after == before
