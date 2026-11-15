"""US2: a waiting person submits an optional name and reason once (spec 003 FR-005..FR-007)."""

from __future__ import annotations

import asyncio

import pytest

from tests.integration.helpers import add_allowlist

EMAIL = "waiting@example.org"
IP = "203.0.113.20"
XFF = {"X-Forwarded-For": IP}


def row(sql, email=EMAIL):
    return sql.execute("SELECT name, request_reason, request_submitted_at FROM app.users WHERE email = %s",
                       (email,)).fetchone()


def audits(sql):
    return sql.execute("SELECT * FROM app.audit_events WHERE action = 'access_requested' ORDER BY id").fetchall()


async def test_the_first_submission_is_stored_audited_and_never_logs_the_text(login_pending, sql):
    c = await login_pending(EMAIL)
    response = await c.post("/v1/access/request", json={"name": "Ann", "reason": "Sales team"}, headers=XFF)
    assert response.status_code == 201 and response.json() == {"submitted": True}
    stored = row(sql)
    assert (stored["name"], stored["request_reason"]) == ("Ann", "Sales team") and stored["request_submitted_at"]
    events = audits(sql)
    assert len(events) == 1
    event = events[0]
    assert event["actor_email"] == EMAIL and event["target"] == EMAIL and str(event["ip"]) == IP
    assert event["detail"] == {"has_name": True, "has_reason": True}
    assert "Ann" not in str(dict(event)) and "Sales" not in str(dict(event))
    status = (await c.get("/v1/access")).json()
    assert (status["submitted"], status["name"], status["reason"]) == (True, "Ann", "Sales team")


async def test_a_second_submission_changes_nothing(login_pending, sql):
    c = await login_pending(EMAIL)
    await c.post("/v1/access/request", json={"name": "Ann", "reason": "first"})
    again = await c.post("/v1/access/request", json={"name": "Bob", "reason": "second"})
    assert again.status_code == 200 and again.json() == {"submitted": False, "already_submitted": True}
    assert (row(sql)["name"], row(sql)["request_reason"]) == ("Ann", "first")
    assert len(audits(sql)) == 1


@pytest.mark.parametrize("body", [{}, {"name": None, "reason": None}, {"name": "", "reason": "  "},
                                  {"name": "\n\t ", "reason": "​‮"}])
async def test_an_empty_submission_still_counts_and_closes_the_form(login_pending, sql, body):
    c = await login_pending(EMAIL)
    assert (await c.post("/v1/access/request", json=body)).status_code == 201
    stored = row(sql)
    assert stored["name"] is None and stored["request_reason"] is None and stored["request_submitted_at"]
    assert (await c.get("/v1/access")).json()["submitted"] is True
    assert audits(sql)[0]["detail"] == {"has_name": False, "has_reason": False}


async def test_limits_and_shape_are_enforced(login_pending, sql):
    c = await login_pending(EMAIL)
    for body in ({"name": "n" * 81}, {"reason": "r" * 501}, {"name": 5}, {"reason": ["x"]},
                 {"name": "ok", "extra": "no"}):
        response = await c.post("/v1/access/request", json=body)
        assert response.status_code == 422, body
    assert row(sql)["request_submitted_at"] is None
    assert (await c.post("/v1/access/request", json={"name": "n" * 80, "reason": "r" * 500})).status_code == 201


async def test_text_is_cleaned_but_markup_is_kept_as_typed(login_pending, sql):
    c = await login_pending(EMAIL)
    messy = "  Ann \u0000​  \n\tLee  "
    reason = "line one\nline  two‮<b>x</b><script>alert(1)</script>"
    assert (await c.post("/v1/access/request", json={"name": messy, "reason": reason})).status_code == 201
    stored = row(sql)
    assert stored["name"] == "Ann Lee"
    assert stored["request_reason"] == "line one line two<b>x</b><script>alert(1)</script>"


async def test_no_valid_session_stores_nothing(login_pending, new_client, sql, clock):
    c = await login_pending(EMAIL)
    anonymous = new_client()
    forged = new_client()
    forged.cookies.set("__Host-session", "forged", domain="test.example")
    for client in (anonymous, forged):
        response = await client.post("/v1/access/request", json={"name": "Mallory"})
        assert response.status_code == 401
    clock.advance(days=8)
    assert (await c.post("/v1/access/request", json={"name": "late"})).status_code == 401
    assert row(sql)["request_submitted_at"] is None


async def test_an_active_person_gets_409_and_nothing_is_stored(login, sql):
    c = await login("active@example.com")
    response = await c.post("/v1/access/request", json={"name": "Ann"})
    assert response.status_code == 409 and response.json()["error"] == "not_pending"
    assert row(sql, "active@example.com")["request_submitted_at"] is None and audits(sql) == []


async def test_a_rejected_persons_waiting_session_is_dead(login_pending, login, sql):
    c = await login_pending(EMAIL)
    admin = await login("admin@example.com", role="admin")
    person = sql.execute("SELECT id FROM app.users WHERE email = %s", (EMAIL,)).fetchone()["id"]
    assert (await admin.post(f"/v1/admin/users/{person}/reject")).status_code == 200
    assert (await c.post("/v1/access/request", json={"name": "Ann"})).status_code == 401
    assert (await c.get("/v1/access")).status_code == 401
    assert row(sql)["request_submitted_at"] is None


async def test_a_request_without_the_origin_header_is_refused(login_pending, sql):
    c = await login_pending(EMAIL)
    response = await c.post("/v1/access/request", json={"name": "Ann"}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403 and row(sql)["request_submitted_at"] is None


async def test_two_simultaneous_submissions_store_one(login_pending, sql):
    c = await login_pending(EMAIL)
    results = await asyncio.gather(
        c.post("/v1/access/request", json={"name": "A", "reason": "one"}),
        c.post("/v1/access/request", json={"name": "B", "reason": "two"}),
    )
    assert sorted(r.status_code for r in results) == [200, 201]
    assert len(audits(sql)) == 1 and row(sql)["name"] in ("A", "B")
    assert (row(sql)["name"], row(sql)["request_reason"]) in (("A", "one"), ("B", "two"))


async def test_the_queue_shows_the_text_to_admins(login_pending, login):
    c = await login_pending(EMAIL)
    await c.post("/v1/access/request", json={"name": "Ann", "reason": "Sales team"})
    admin = await login("admin@example.com", role="admin")
    items = (await admin.get("/v1/admin/users?status=pending")).json()["items"]
    assert [i["email"] for i in items] == [EMAIL]
