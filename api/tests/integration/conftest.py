"""Integration fixtures: real Postgres and Redis (isolated project), in-process app, fake clock."""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import httpx
import psycopg
import pytest
import redis as sync_redis

from app.mailer import CaptureMailer
from app.main import create_app
from tests.conftest import ORIGIN


def _app_owner_conninfo(settings) -> str:
    password = Path("/run/secrets/app_owner_password").read_text(encoding="utf-8").strip()
    return (
        f"host={settings.postgres_host} port={settings.postgres_port} dbname={settings.postgres_db} "
        f"user=app_owner password={password}"
    )


@pytest.fixture(autouse=True)
def clean_state(base_settings):
    """Empty the auth tables and Redis before every test (isolated project only)."""
    with psycopg.connect(_app_owner_conninfo(base_settings), autocommit=True) as conn:
        conn.execute(
            "TRUNCATE app.sessions, app.email_codes, app.allowlist, app.audit_events, app.users "
            "RESTART IDENTITY CASCADE"
        )
    sync_redis.Redis.from_url(base_settings.redis_url).flushdb()
    yield


@pytest.fixture
def sql(base_settings):
    """Autocommit connection as app_owner for arranging and checking data in tests."""
    with psycopg.connect(_app_owner_conninfo(base_settings), autocommit=True, row_factory=psycopg.rows.dict_row) as conn:
        yield conn


@pytest.fixture
def capture_dir(tmp_path) -> Path:
    directory = tmp_path / "mail"
    directory.mkdir()
    return directory


@pytest.fixture
def settings(base_settings, capture_dir):
    return dataclasses.replace(base_settings, mail_capture_dir=str(capture_dir))


@pytest.fixture
def mailer(settings, clock, capture_dir) -> CaptureMailer:
    return CaptureMailer(capture_dir, clock, settings.mail_capture_ttl_minutes)


@pytest.fixture
def identity():
    """Replaced by a fake identity provider in the Google tests."""
    return None


@pytest.fixture
async def app(settings, clock, mailer, identity):
    application = create_app(settings=settings, clock=clock, mailer=mailer, identity=identity)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers={"Origin": ORIGIN}) as c:
        yield c


@pytest.fixture
def captured_logs(caplog):
    """Everything the app logged during the test, as one string."""
    caplog.set_level(logging.DEBUG)
    return lambda: caplog.text
