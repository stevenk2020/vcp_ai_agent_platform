"""GET /v1/access: a person's own status; the only read a waiting session may do (contracts/http-api.md)."""

from __future__ import annotations

from tests.integration.helpers import add_user

PENDING = "waiting@example.org"
ACTIVE = "active@example.com"


async def test_an_active_person_sees_status_active_and_the_role(login):
    c = await login(ACTIVE, role="builder")
    response = await c.get("/v1/access")
    assert response.status_code == 200
    assert response.json() == {"email": ACTIVE, "status": "active", "role": "builder", "submitted": False,
                               "name": None, "reason": None}


async def test_a_waiting_person_sees_status_pending_and_no_role(login_pending):
    c = await login_pending(PENDING)
    body = (await c.get("/v1/access")).json()
    assert body == {"email": PENDING, "status": "pending", "role": None, "submitted": False, "name": None, "reason": None}


async def test_every_kind_of_no_session_gets_the_same_401(login_pending, new_client, sql, clock):
    waiting = await login_pending(PENDING)
    sql.execute("UPDATE app.users SET status = 'suspended', suspended_reason = 'admin' WHERE email = %s", (PENDING,))
    suspended = await waiting.get("/v1/access")
    sql.execute("UPDATE app.users SET status = 'rejected', suspended_reason = NULL WHERE email = %s", (PENDING,))
    rejected = await waiting.get("/v1/access")
    anonymous = await new_client().get("/v1/access")
    forged_client = new_client()
    forged_client.cookies.set("__Host-session", "forged", domain="test.example")
    forged = await forged_client.get("/v1/access")
    for response in (suspended, rejected, anonymous, forged):
        assert response.status_code == 401 and response.json() == anonymous.json()
        assert response.json()["error"] == "unauthenticated"


async def test_an_expired_and_a_revoked_session_are_refused(login, clock, sql):
    expired = await login(ACTIVE)
    clock.advance(days=8)
    assert (await expired.get("/v1/access")).status_code == 401
    other = await login("second@example.com")
    sql.execute("UPDATE app.sessions SET revoked_at = now(), revoked_reason = 'signed_out' WHERE revoked_at IS NULL "
                "AND expires_at > %s", (clock.now(),))
    assert (await other.get("/v1/access")).status_code == 401


async def test_approval_makes_the_same_cookie_work_with_the_chosen_role(login_pending, login, sql):
    waiting = await login_pending(PENDING)
    admin = await login("admin@example.com", role="admin")
    person = sql.execute("SELECT id FROM app.users WHERE email = %s", (PENDING,)).fetchone()["id"]
    assert (await admin.post(f"/v1/admin/users/{person}/approve", json={"role": "builder"})).status_code == 200
    body = (await waiting.get("/v1/access")).json()
    assert (body["status"], body["role"]) == ("active", "builder")
    assert (await waiting.get("/v1/me")).status_code == 200  # no second sign-in needed


async def test_the_response_never_contains_the_session_token(login_pending):
    c = await login_pending(PENDING)
    token = c.cookies.get("__Host-session")
    assert token and token not in (await c.get("/v1/access")).text
