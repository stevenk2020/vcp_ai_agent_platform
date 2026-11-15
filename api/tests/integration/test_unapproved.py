"""US3: people who are not approved are kept out without learning who is approved."""

from __future__ import annotations

import dataclasses
import statistics
import time

import httpx

from app.main import create_app
from tests.conftest import ORIGIN
from tests.integration.helpers import add_allowlist, add_user, read_code

ALLOWED = "allowed@example.com"
STRANGER = "stranger@example.org"


async def request_code(client, email):
    return await client.post("/v1/auth/code/request", json={"email": email})


async def verify(client, mailer, email, code=None):
    return await client.post("/v1/auth/code/verify", json={"email": email, "code": code or read_code(mailer, email)})


async def full_sign_in(new_client, mailer, email):
    c = new_client()
    assert (await request_code(c, email)).status_code == 202
    response = await verify(c, mailer, email)
    return c, response


def comparable(response):
    headers = {k: v for k, v in response.headers.items() if k.lower() not in {"date"}}
    return response.status_code, headers, response.json()


async def test_the_request_response_is_identical_for_allowed_and_unknown_addresses(client, mailer, sql):
    add_allowlist(sql, ALLOWED)
    a = await request_code(client, ALLOWED)
    b = await request_code(client, STRANGER)
    assert comparable(a) == comparable(b)
    assert len(mailer.messages(ALLOWED)) == 1 and len(mailer.messages(STRANGER)) == 1


class NullMailer:
    async def send_code(self, to, code, minutes):
        return None


async def test_response_times_do_not_reveal_who_is_allowed(settings, clock, sql):
    for i in range(100):
        add_allowlist(sql, f"allowed{i}@example.com")
    roomy = dataclasses.replace(settings, code_rate_per_email=10**6, code_rate_per_ip=10**6)
    application = create_app(settings=roomy, clock=clock, mailer=NullMailer())
    allowed_times, unknown_times = [], []
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers={"Origin": ORIGIN}) as c:
            for i in range(100):
                for bucket, email in ((allowed_times, f"allowed{i}@example.com"), (unknown_times, f"unknown{i}@example.org")):
                    start = time.perf_counter()
                    response = await request_code(c, email)
                    bucket.append(time.perf_counter() - start)
                    assert response.status_code == 202
    a, u = statistics.median(allowed_times), statistics.median(unknown_times)
    assert abs(a - u) / max(a, u) < 0.20, (a, u)


async def test_an_unknown_person_who_proves_the_email_becomes_pending_with_only_a_waiting_session(new_client, mailer, sql, login):
    c, response = await full_sign_in(new_client, mailer, STRANGER)
    assert response.status_code == 200 and response.json() == {"status": "pending"}
    user = sql.execute("SELECT id, status, role FROM app.users WHERE email = %s", (STRANGER,)).fetchone()
    assert (user["status"], user["role"]) == ("pending", "user")
    # a waiting session exists for exactly this person ...
    sessions = sql.execute("SELECT user_id FROM app.sessions").fetchall()
    assert [s["user_id"] for s in sessions] == [user["id"]]
    # ... and its cookie is as hardened as any session cookie (FR-017)
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith("__host-session=")
    for flag in ("httponly", "secure", "samesite=lax", "path=/"):
        assert flag in cookie, flag
    # ... but it opens nothing protected
    assert (await c.get("/v1/me")).status_code == 401
    assert (await c.get("/v1/admin/users")).status_code == 401
    assert (await c.get("/v1/admin/audit")).status_code == 401

    admin = await login("admin@example.com", role="admin")
    queue = (await admin.get("/v1/admin/users?status=pending")).json()["items"]
    assert [u["email"] for u in queue] == [STRANGER]
    actions = [r["action"] for r in sql.execute("SELECT action FROM app.audit_events ORDER BY id").fetchall()]
    assert "signin_pending" in actions and "session_created" in actions


async def test_signing_in_again_while_pending_adds_no_second_entry(new_client, mailer, sql):
    for _ in range(2):
        c, response = await full_sign_in(new_client, mailer, STRANGER)
        assert response.json()["status"] == "pending" and "__Host-session" in c.cookies
        assert (await c.get("/v1/me")).status_code == 401
    assert sql.execute("SELECT count(*) AS n FROM app.users WHERE email = %s", (STRANGER,)).fetchone()["n"] == 1


async def test_waiting_session_growth_is_bounded_by_the_code_and_verify_caps(new_client, mailer, sql):
    """One pending address can only obtain 5 codes per hour (and each verify counts against the per-address cap of 30
    per 10 minutes), so waiting sessions cannot be minted without bound (research R-3)."""
    for _ in range(5):
        c, response = await full_sign_in(new_client, mailer, STRANGER)
        assert response.json() == {"status": "pending"}
    assert (await request_code(new_client(), STRANGER)).status_code == 429
    assert sql.execute("SELECT count(*) AS n FROM app.sessions").fetchone()["n"] == 5


async def test_adding_a_matching_entry_lets_a_pending_person_in_at_the_next_sign_in(new_client, mailer, sql):
    await full_sign_in(new_client, mailer, STRANGER)
    add_allowlist(sql, STRANGER, role="builder")
    waiting_client, _ = await full_sign_in(new_client, mailer, STRANGER)
    c, response = await full_sign_in(new_client, mailer, STRANGER)
    assert response.json() == {"status": "signed_in", "role": "builder"}
    assert (await c.get("/v1/me")).json()["role"] == "builder"
    # the earlier waiting session starts working as soon as the person is active (status is re-read every request)
    assert (await waiting_client.get("/v1/me")).status_code == 200
    assert sql.execute("SELECT count(*) AS n FROM app.users WHERE email = %s", (STRANGER,)).fetchone()["n"] == 1


async def test_a_person_suspended_by_an_admin_or_rejected_stays_blocked_even_when_allowlisted(new_client, mailer, sql):
    add_user(sql, "sus@example.com", "suspended", reason="admin")
    add_user(sql, "rej@example.com", "rejected")
    for email in ("sus@example.com", "rej@example.com"):
        add_allowlist(sql, email)
        c, response = await full_sign_in(new_client, mailer, email)
        assert response.json() == {"status": "refused"}, email
        assert "set-cookie" not in response.headers and (await c.get("/v1/me")).status_code == 401
    assert "signin_refused" in [r["action"] for r in sql.execute("SELECT action FROM app.audit_events").fetchall()]


async def test_suspension_by_allowlist_removal_ends_when_an_entry_matches_again(new_client, mailer, sql):
    add_user(sql, ALLOWED, "suspended", role="user", reason="allowlist_removed")
    c, blocked = await full_sign_in(new_client, mailer, ALLOWED)
    assert blocked.json() == {"status": "refused"}  # no entry yet
    add_allowlist(sql, ALLOWED, role="builder")
    c, response = await full_sign_in(new_client, mailer, ALLOWED)
    assert response.json() == {"status": "signed_in", "role": "builder"}
    user = sql.execute("SELECT status, role, suspended_reason FROM app.users WHERE email = %s", (ALLOWED,)).fetchone()
    assert (user["status"], user["role"], user["suspended_reason"]) == ("active", "builder", None)


async def test_removing_and_re_adding_an_entry_restores_access_but_old_sessions_stay_revoked(login, new_client, mailer):
    admin = await login("admin@example.com", role="admin")
    entry = (await admin.post("/v1/admin/allowlist", json={"kind": "email", "value": ALLOWED, "default_role": "user"})).json()
    old = await login(ALLOWED, entry=False)
    assert (await admin.delete(f"/v1/admin/allowlist/{entry['id']}")).status_code == 204
    assert (await old.get("/v1/me")).status_code == 401

    assert (await admin.post("/v1/admin/allowlist", json={"kind": "email", "value": ALLOWED, "default_role": "user"})).status_code == 201
    fresh, response = await full_sign_in(new_client, mailer, ALLOWED)
    assert response.json()["status"] == "signed_in"
    assert (await fresh.get("/v1/me")).status_code == 200
    assert (await old.get("/v1/me")).status_code == 401  # the earlier session stays revoked


async def test_every_failed_verification_looks_the_same(client, mailer, clock, sql):
    add_allowlist(sql, ALLOWED)
    await request_code(client, ALLOWED)
    code = read_code(mailer, ALLOWED)
    wrong = await client.post("/v1/auth/code/verify", json={"email": ALLOWED, "code": "000000" if code != "000000" else "111111"})
    unknown = await client.post("/v1/auth/code/verify", json={"email": "nobody@example.com", "code": "123456"})
    assert (await verify(client, mailer, ALLOWED, code)).status_code == 200
    consumed = await client.post("/v1/auth/code/verify", json={"email": ALLOWED, "code": code})
    await request_code(client, ALLOWED)
    stale = read_code(mailer, ALLOWED)
    clock.advance(minutes=11)
    expired = await client.post("/v1/auth/code/verify", json={"email": ALLOWED, "code": stale})
    bodies = [r.json() for r in (wrong, unknown, consumed, expired)]
    assert all(r.status_code == 401 for r in (wrong, unknown, consumed, expired))
    assert all(b == bodies[0] for b in bodies)
