"""US7: one audit record per event, readable by admins, and no readable secret anywhere."""

from __future__ import annotations

import dataclasses
import json
import logging
import re

import httpx
import psycopg
import pytest

from app import audit
from app.cli import create_first_admin
from app.mailer import MailSendError
from app.main import create_app
from tests.conftest import ORIGIN
from tests.fakes import FakeIdentityProvider
from tests.integration.helpers import add_allowlist, add_user, read_code

ADMIN = "admin@example.com"
ALICE = "alice@example.com"
IP = "203.0.113.50"
XFF = {"X-Forwarded-For": IP}


@pytest.fixture
def identity():
    return FakeIdentityProvider()


def events(sql, action):
    return sql.execute("SELECT * FROM app.audit_events WHERE action = %s ORDER BY id", (action,)).fetchall()


def only(sql, action, outcome="success"):
    rows = events(sql, action)
    assert len(rows) == 1, f"expected exactly one {action} event, found {len(rows)}"
    row = rows[0]
    assert row["outcome"] == outcome and row["at"] is not None
    return row


async def code_flow(client, mailer, email, headers=None):
    await client.post("/v1/auth/code/request", json={"email": email}, headers=headers or {})
    return await client.post("/v1/auth/code/verify", json={"email": email, "code": read_code(mailer, email)},
                             headers=headers or {})


# ---- one scenario per action name (data-model.md "Action names") ---------------------------------

async def test_code_requested_and_code_verified_and_session_created(client, mailer, sql):
    add_allowlist(sql, ALICE)
    await code_flow(client, mailer, ALICE, XFF)
    requested = only(sql, "code_requested")
    assert requested["target"] == ALICE and str(requested["ip"]) == IP
    verified = only(sql, "code_verified")
    assert verified["actor_email"] == ALICE and verified["actor_user_id"] and str(verified["ip"]) == IP
    assert str(only(sql, "session_created")["ip"]) == IP


async def test_code_failed_and_code_locked(client, mailer, sql):
    add_allowlist(sql, ALICE)
    await client.post("/v1/auth/code/request", json={"email": ALICE})
    code = read_code(mailer, ALICE)
    wrong = "000000" if code != "000000" else "111111"
    await client.post("/v1/auth/code/verify", json={"email": ALICE, "code": wrong}, headers=XFF)
    only(sql, "code_failed", "failure")
    for _ in range(4):
        await client.post("/v1/auth/code/verify", json={"email": ALICE, "code": wrong}, headers=XFF)
    assert len(events(sql, "code_failed")) == 5
    only(sql, "code_locked", "failure")


async def test_code_send_failed(settings, clock, sql):
    class Failing:
        async def send_code(self, to, code, minutes):
            raise MailSendError("boom")

    application = create_app(settings=settings, clock=clock, mailer=Failing())
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url=ORIGIN,
                                     headers={"Origin": ORIGIN}) as c:
            await c.post("/v1/auth/code/request", json={"email": ALICE}, headers=XFF)
    only(sql, "code_send_failed", "failure")


async def test_google_signin_pending_and_refused(client, identity, sql):
    add_allowlist(sql, ALICE)
    for email in (ALICE, "newcomer@example.org", "banned@example.com"):
        if email == "banned@example.com":
            add_user(sql, email, "suspended", reason="admin")
        location = (await client.get("/v1/auth/google/start")).headers["location"]
        from urllib.parse import parse_qs, urlparse

        flow_state = parse_qs(urlparse(location).query)["state"][0]
        identity.email = email
        await client.get("/v1/auth/google/callback", params={"code": "c", "state": flow_state}, headers=XFF)
    assert len(events(sql, "google_signin")) == 3
    assert {e["actor_email"] for e in events(sql, "google_signin") if e["actor_email"]} >= {ALICE}
    only(sql, "signin_pending")
    only(sql, "signin_refused", "failure")
    # the pending person got a waiting session, and that is audited like any other session
    pending_session = [e for e in events(sql, "session_created") if e["actor_email"] == "newcomer@example.org"]
    assert len(pending_session) == 1


async def test_rate_limited(client, sql):
    for _ in range(6):
        await client.post("/v1/auth/code/request", json={"email": ALICE}, headers=XFF)
    only(sql, "rate_limited", "failure")


async def test_signed_out(client, mailer, sql):
    add_allowlist(sql, ALICE)
    await code_flow(client, mailer, ALICE)
    await client.post("/v1/auth/logout", headers=XFF)
    row = only(sql, "signed_out")
    assert row["actor_email"] == ALICE and str(row["ip"]) == IP


async def test_admin_actions_are_audited_with_actor_and_address(login, sql):
    admin = await login(ADMIN, role="admin")
    alice = await login(ALICE)
    alice_id = (await alice.get("/v1/me")).json()["id"]
    pending_id = add_user(sql, "p@example.com", "pending")
    reject_id = add_user(sql, "r@example.com", "pending")

    entry = (await admin.post("/v1/admin/allowlist", json={"kind": "email", "value": "n@example.com", "default_role": "user"},
                              headers=XFF)).json()
    await admin.delete(f"/v1/admin/allowlist/{entry['id']}", headers=XFF)
    await admin.post(f"/v1/admin/users/{pending_id}/approve", json={"role": "user"}, headers=XFF)
    await admin.post(f"/v1/admin/users/{reject_id}/reject", headers=XFF)
    await admin.post(f"/v1/admin/users/{alice_id}/role", json={"role": "builder"}, headers=XFF)
    await admin.post(f"/v1/admin/users/{alice_id}/suspend", headers=XFF)

    for action in ("allowlist_removed", "user_approved", "user_rejected", "user_suspended", "role_changed"):
        row = only(sql, action)
        assert row["actor_email"] == ADMIN and str(row["ip"]) == IP, action
    assert len([e for e in events(sql, "allowlist_added") if e["target"] == "email:n@example.com"]) == 1
    only(sql, "session_revoked")  # alice's one session, revoked by the suspension


async def test_last_admin_blocked(login, sql):
    admin = await login(ADMIN, role="admin")
    admin_id = (await admin.get("/v1/me")).json()["id"]
    await admin.post(f"/v1/admin/users/{admin_id}/suspend", headers=XFF)
    row = only(sql, "last_admin_blocked", "failure")
    assert row["actor_email"] == ADMIN


async def test_first_admin_events(settings, clock, sql):
    await create_first_admin(settings, clock, ADMIN)
    assert only(sql, "first_admin_created")["actor_email"] == "cli"
    await create_first_admin(settings, clock, "other@example.com")
    assert only(sql, "first_admin_refused", "failure")["actor_email"] == "cli"


def test_every_action_name_has_a_scenario_above():
    covered = {
        "code_requested", "code_send_failed", "code_verified", "code_failed", "code_locked", "google_signin",
        "signin_pending", "signin_refused", "rate_limited", "session_created", "session_revoked", "signed_out",
        "allowlist_added", "allowlist_removed", "user_approved", "user_rejected", "user_suspended", "role_changed",
        "first_admin_created", "first_admin_refused", "last_admin_blocked",
    }
    assert covered == set(audit.ACTIONS)


# ---- reading the trail --------------------------------------------------------------------------

def seed(sql, n):
    for i in range(n):
        sql.execute(
            "INSERT INTO app.audit_events (at, actor_email, action, target, outcome, ip, detail) "
            "VALUES (now() + make_interval(secs => %s), 'seed@example.com', %s, %s, 'success', '10.0.0.1', '{}'::jsonb)",
            (i, "code_requested" if i % 2 == 0 else "signed_out", f"t{i}"),
        )


async def test_the_audit_endpoint_pages_newest_first_and_filters(login, sql):
    admin = await login(ADMIN, role="admin")
    seed(sql, 12)
    page = (await admin.get("/v1/admin/audit?limit=5&action=code_requested")).json()["items"]
    assert len(page) == 5 and all(i["action"] == "code_requested" for i in page)
    ids = [i["id"] for i in page]
    assert ids == sorted(ids, reverse=True)
    older = (await admin.get(f"/v1/admin/audit?limit=100&action=code_requested&before={ids[-1]}")).json()["items"]
    assert older and all(i["id"] < ids[-1] for i in older)
    item = page[0]
    assert set(item) >= {"id", "at", "actor_user_id", "actor_email", "action", "target", "outcome", "ip", "detail"}


async def test_audit_limit_bounds_and_roles(login, new_client):
    admin = await login(ADMIN, role="admin")
    assert (await admin.get("/v1/admin/audit?limit=0")).status_code == 422
    assert (await admin.get("/v1/admin/audit?limit=501")).status_code == 422
    assert (await admin.get("/v1/admin/audit?limit=500")).status_code == 200
    user = await login(ALICE)
    assert (await user.get("/v1/admin/audit")).status_code == 403
    assert (await new_client().get("/v1/admin/audit")).status_code == 401


def test_the_api_role_cannot_change_or_delete_audit_rows(settings, sql):
    sql.execute("INSERT INTO app.audit_events (at, action, outcome) VALUES (now(), 'signed_out', 'success')")
    password = open("/run/secrets/api_rw_password", encoding="utf-8").read().strip()
    with psycopg.connect(host=settings.postgres_host, dbname=settings.postgres_db, user="api_rw", password=password,
                         autocommit=True) as conn:
        for statement in ("UPDATE app.audit_events SET outcome = 'failure'", "DELETE FROM app.audit_events",
                          "TRUNCATE app.audit_events"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


# ---- no readable code, session token or Google token anywhere -------------------------------------

async def test_no_secret_is_stored_or_logged_in_readable_form(client, identity, mailer, sql, caplog, monkeypatch):
    from urllib.parse import parse_qs, urlparse

    import app.auth.service as service

    secret_code = "913577"
    monkeypatch.setattr(service, "new_code", lambda: secret_code)
    caplog.set_level(logging.DEBUG)
    add_allowlist(sql, ALICE)
    add_allowlist(sql, "gina@example.com")

    # code sign-in, then a Google sign-in, then admin work
    response = await code_flow(client, mailer, ALICE)
    assert response.json()["status"] == "signed_in"
    session_token = client.cookies.get("__Host-session")

    start = await client.get("/v1/auth/google/start")
    flow_state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    oauth_cookie = client.cookies.get("__Host-oauth")
    verifier = oauth_cookie.split(".")[1]
    google_code = "G-AUTH-CODE-SECRET-777"
    identity.email = "gina@example.com"
    await client.get("/v1/auth/google/callback", params={"code": google_code, "state": flow_state})
    google_session = client.cookies.get("__Host-session")

    # a pending person's waiting session token is just as unreadable in storage and logs
    identity.email = "pending-person@example.org"
    start = await client.get("/v1/auth/google/start")
    pending_state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    await client.get("/v1/auth/google/callback", params={"code": "pending-code", "state": pending_state})
    pending_token = client.cookies.get("__Host-session")
    assert pending_token and pending_token != google_session

    secrets = {"session token": session_token, "google session token": google_session, "pending session token": pending_token,
               "google code": google_code,
               "pkce verifier": verifier, "oauth state": flow_state}
    dumps = []
    for table in ("users", "allowlist", "email_codes", "sessions", "audit_events"):
        dumps.append(repr([dict(r) for r in sql.execute(f"SELECT * FROM app.{table}").fetchall()]))
    database_text = "\n".join(dumps)
    app_logs = "\n".join(
        r.getMessage() + json.dumps({k: v for k, v in r.__dict__.items() if k not in ("args", "msg", "exc_info")}, default=str)
        for r in caplog.records if r.name.startswith("app")
    )
    for label, value in secrets.items():
        assert value and value not in database_text, f"{label} is stored readable"
        assert value not in app_logs, f"{label} is logged"
    assert secret_code not in database_text, "a sign-in code is stored readable"
    assert re.search(rf"(?<![0-9]){secret_code}(?![0-9])", app_logs) is None, "a sign-in code is logged"
    # the only readable copy of a code is the development mail sink, which is not part of the stored data
    assert any(m["code"] == secret_code for m in mailer.messages(ALICE))
