"""US4: sign in with Google, using a fake identity provider (no real Google needed)."""

from __future__ import annotations

import dataclasses
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.main import create_app
from tests.conftest import ORIGIN
from tests.fakes import FakeIdentityProvider
from tests.integration.helpers import add_allowlist, add_user

EMAIL = "gina@example.com"
SECRET_CODE = "SUPER-SECRET-AUTH-CODE-12345"


@pytest.fixture
def identity():
    return FakeIdentityProvider()


async def start(client):
    response = await client.get("/v1/auth/google/start")
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    return response, query["state"][0]


async def google_flow(client, fake, email, state=None, code=SECRET_CODE):
    _, flow_state = await start(client)
    fake.email = email
    return await client.get("/v1/auth/google/callback", params={"code": code, "state": state or flow_state})


async def test_start_redirects_to_the_provider_with_pkce_and_sets_a_state_cookie(client):
    response, state = await start(client)
    location = response.headers["location"]
    query = parse_qs(urlparse(location).query)
    assert location.startswith("https://accounts.fake.example/auth")
    assert query["redirect_uri"] == [f"{ORIGIN}/v1/auth/google/callback"] and query["code_challenge"][0]
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith("__host-oauth=") and "httponly" in cookie and "secure" in cookie
    assert state


async def test_a_verified_allowlisted_email_gets_a_session(client, identity, sql):
    add_allowlist(sql, EMAIL, role="builder")
    response = await google_flow(client, identity, EMAIL)
    assert response.status_code == 302 and response.headers["location"] == "/?signin=ok"
    me = (await client.get("/v1/me")).json()
    assert (me["email"], me["role"]) == (EMAIL, "builder")
    assert identity.seen and identity.seen[0]["verifier_len"] >= 43


async def test_a_verified_unknown_email_becomes_pending_with_a_waiting_session(client, identity, sql):
    response = await google_flow(client, identity, "newcomer@example.org")
    assert response.headers["location"] == "/?signin=pending"
    cookie = response.headers["set-cookie"].lower()
    assert "__host-session=" in cookie and "httponly" in cookie and "secure" in cookie
    assert "__Host-session" in client.cookies          # a waiting session exists ...
    assert (await client.get("/v1/me")).status_code == 401  # ... but it opens nothing protected
    assert (await client.get("/v1/admin/users")).status_code == 401
    row = sql.execute("SELECT id, status FROM app.users WHERE email = 'newcomer@example.org'").fetchone()
    assert row["status"] == "pending"
    assert sql.execute("SELECT count(*) AS n FROM app.sessions WHERE user_id = %s", (row["id"],)).fetchone()["n"] == 1


async def test_a_suspended_person_is_refused(client, identity, sql):
    add_user(sql, EMAIL, "suspended", reason="admin")
    add_allowlist(sql, EMAIL)
    response = await google_flow(client, identity, EMAIL)
    assert response.headers["location"] == "/?signin=refused" and "__Host-session" not in client.cookies


async def test_an_unverified_email_is_refused(client, identity, sql):
    add_allowlist(sql, EMAIL)
    identity.error = "email is not verified"
    response = await google_flow(client, identity, EMAIL)
    assert response.headers["location"] == "/?signin=error"
    assert "__Host-session" not in client.cookies
    assert sql.execute("SELECT count(*) AS n FROM app.users").fetchone()["n"] == 0


async def test_bad_missing_or_expired_state_and_provider_errors_all_fail(client, identity, clock, sql):
    add_allowlist(sql, EMAIL)
    wrong = await google_flow(client, identity, EMAIL, state="not-the-state")
    assert wrong.headers["location"] == "/?signin=error"

    no_cookie = httpx.AsyncClient(transport=client._transport, base_url=ORIGIN)
    async with no_cookie:
        identity.email = EMAIL
        missing = await no_cookie.get("/v1/auth/google/callback", params={"code": "c", "state": "s"})
    assert missing.headers["location"] == "/?signin=error"

    denied = await client.get("/v1/auth/google/callback", params={"error": "access_denied"})
    assert denied.headers["location"] == "/?signin=error"

    _, state = await start(client)
    identity.email = EMAIL
    clock.advance(minutes=11)
    expired = await client.get("/v1/auth/google/callback", params={"code": "c", "state": state})
    assert expired.headers["location"] == "/?signin=error"
    assert "__Host-session" not in client.cookies
    assert "google_signin" in [r["action"] for r in sql.execute("SELECT action FROM app.audit_events").fetchall()]


async def test_the_same_email_by_code_and_by_google_is_one_user(login, new_client, identity, sql):
    first = await login(EMAIL, role="user")
    me = (await first.get("/v1/me")).json()
    other = new_client()
    response = await google_flow(other, identity, EMAIL)
    assert response.headers["location"] == "/?signin=ok"
    assert (await other.get("/v1/me")).json()["id"] == me["id"]
    assert sql.execute("SELECT count(*) AS n FROM app.users").fetchone()["n"] == 1


async def test_without_a_google_client_both_endpoints_say_not_configured(settings, clock, mailer):
    unconfigured = dataclasses.replace(settings, google_client_id="", google_client_secret="")
    application = create_app(settings=unconfigured, clock=clock, mailer=mailer)
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as c:
            for path in ("/v1/auth/google/start", "/v1/auth/google/callback?code=a&state=b"):
                response = await c.get(path)
                assert response.status_code == 503 and response.json()["error"] == "not_configured"


async def test_no_google_token_or_code_is_stored_or_logged_by_the_app(client, identity, sql, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    add_allowlist(sql, EMAIL)
    await google_flow(client, identity, EMAIL)
    app_logs = "\n".join(r.getMessage() + json.dumps(getattr(r, "__dict__", {}), default=str)
                         for r in caplog.records if r.name.startswith("app"))
    assert SECRET_CODE not in app_logs
    for table in ("users", "allowlist", "email_codes", "sessions", "audit_events"):
        dump = json.dumps([dict(r) for r in sql.execute(f"SELECT * FROM app.{table}").fetchall()], default=str)
        assert SECRET_CODE not in dump, table
