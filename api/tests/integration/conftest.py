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
async def new_client(app):
    """Factory for additional clients, each with its own cookie jar (several people at once)."""
    created = []

    def make(origin: str | None = ORIGIN) -> httpx.AsyncClient:
        headers = {"Origin": origin} if origin else {}
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN, headers=headers)
        created.append(c)
        return c

    yield make
    for c in created:
        await c.aclose()


@pytest.fixture
def login(new_client, mailer, sql):
    """Sign a person in through the real flow (adds an allowlist entry for them first)."""
    from tests.integration.helpers import add_allowlist, read_code

    async def do(email: str, role: str = "user", entry: bool = True):
        if entry:
            add_allowlist(sql, email, role=role)
        c = new_client()
        assert (await c.post("/v1/auth/code/request", json={"email": email})).status_code == 202
        response = await c.post("/v1/auth/code/verify", json={"email": email, "code": read_code(mailer, email)})
        assert response.status_code == 200 and response.json()["status"] == "signed_in", response.text
        return c

    return do


@pytest.fixture
def login_pending(new_client, mailer):
    """Sign in a person who is NOT on the allowlist through the real code flow: they end pending and hold a
    waiting session. Returns the client with that cookie."""
    from tests.integration.helpers import read_code

    async def do(email: str):
        c = new_client()
        assert (await c.post("/v1/auth/code/request", json={"email": email})).status_code == 202
        response = await c.post("/v1/auth/code/verify", json={"email": email, "code": read_code(mailer, email)})
        assert response.status_code == 200 and response.json() == {"status": "pending"}, response.text
        assert "__Host-session" in c.cookies
        return c

    return do


@pytest.fixture
def captured_logs(caplog):
    """Everything the app logged during the test, as one string."""
    caplog.set_level(logging.DEBUG)
    return lambda: caplog.text
