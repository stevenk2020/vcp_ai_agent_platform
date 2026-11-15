"""US1: the base stack comes up healthy, keeps its data, and exposes only what it should."""

from __future__ import annotations

import json
import time

import pytest

from conftest import compose, connect

REQUIRED_SERVICES = ("postgres", "redis", "sync")


def _ps() -> dict[str, dict]:
    out = compose("ps", "--all", "--format", "json").stdout
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    return {row["Service"]: row for row in rows}


def _wait_healthy(service: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = _ps().get(service)
        if row and row.get("Health") == "healthy":
            return
        time.sleep(2)
    pytest.fail(f"{service} did not become healthy within {timeout:.0f}s")


@pytest.mark.parametrize("service", REQUIRED_SERVICES)
def test_service_is_healthy(service):
    row = _ps().get(service)
    assert row is not None, f"{service} is not part of the running stack"
    assert row["State"] == "running"
    assert row["Health"] == "healthy"


def test_postgres_publishes_loopback_only_and_redis_publishes_nothing():
    services = _ps()
    for publisher in services["postgres"].get("Publishers") or []:
        if publisher.get("PublishedPort"):
            assert publisher["URL"] == "127.0.0.1", publisher
    redis_published = [p for p in services["redis"].get("Publishers") or [] if p.get("PublishedPort")]
    assert redis_published == []


def test_data_survives_stop_and_start():
    marker = f"marker-{int(time.time())}"
    with connect("postgres") as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS scratch")
        conn.execute("CREATE TABLE IF NOT EXISTS scratch.marker (value text PRIMARY KEY)")
        conn.execute("INSERT INTO scratch.marker (value) VALUES (%s)", (marker,))

    compose("stop", "postgres")
    compose("start", "postgres")
    _wait_healthy("postgres")

    with connect("postgres") as conn:
        rows = conn.execute("SELECT value FROM scratch.marker WHERE value = %s", (marker,)).fetchall()
        assert rows == [(marker,)]
        conn.execute("DROP SCHEMA scratch CASCADE")


def test_sync_service_idles_without_a_source_secret_and_says_so():
    """Feature 004: with an empty secrets/source_sqlserver the worker is healthy and idle, never in a crash loop."""
    from conftest import SECRETS_DIR

    secret = SECRETS_DIR / "source_sqlserver"
    if secret.is_file() and secret.stat().st_size > 0:
        pytest.skip("the source secret is filled: the worker is expected to run real syncs")
    row = _ps()["sync"]
    assert row["State"] == "running" and row["Health"] == "healthy"
    logs = compose("logs", "--no-log-prefix", "sync").stdout
    assert "source_not_configured" in logs
