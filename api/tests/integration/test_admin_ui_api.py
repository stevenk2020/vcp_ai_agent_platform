"""Additive admin API behaviour used by the pages (contracts/http-api.md)."""

from __future__ import annotations

import asyncio

import pytest

from tests.integration.helpers import add_user

ADMIN = "admin@example.com"


async def test_users_carry_the_request_text_and_keep_every_old_field(login, login_pending):
    waiting = await login_pending("w@example.org")
    await waiting.post("/v1/access/request", json={"name": "Wendy", "reason": "needs reports"})
    admin = await login(ADMIN, role="admin")
    items = (await admin.get("/v1/admin/users?status=pending")).json()["items"]
    assert len(items) == 1
    item = items[0]
    assert set(item) >= {"id", "email", "role", "status", "created_at", "last_login_at",
                         "name", "request_reason", "request_submitted_at"}
    assert (item["name"], item["request_reason"]) == ("Wendy", "needs reports") and item["request_submitted_at"]
    everyone = (await admin.get("/v1/admin/users")).json()["items"]
    plain = next(i for i in everyone if i["email"] == ADMIN)
    assert plain["name"] is None and plain["request_reason"] is None and plain["request_submitted_at"] is None


async def test_the_queue_is_ordered_by_creation_time(login, sql):
    for email in ("b@example.org", "a@example.org", "c@example.org"):
        add_user(sql, email, "pending")
    admin = await login(ADMIN, role="admin")
    emails = [i["email"] for i in (await admin.get("/v1/admin/users?status=pending")).json()["items"]]
    assert emails == ["b@example.org", "a@example.org", "c@example.org"]


@pytest.mark.parametrize("action,body", [("approve", {"role": "user"}), ("reject", None)])
async def test_expect_status_refuses_a_person_someone_else_already_handled(login, sql, action, body):
    person = add_user(sql, "p@example.org", "pending")
    admin = await login(ADMIN, role="admin")
    first = await admin.post(f"/v1/admin/users/{person}/reject?expect_status=pending")
    assert first.status_code == 200
    before = sql.execute("SELECT count(*) AS n FROM app.audit_events").fetchone()["n"]
    second = await admin.post(f"/v1/admin/users/{person}/{action}?expect_status=pending", json=body)
    assert second.status_code == 409 and second.json()["error"] == "already_handled"
    assert sql.execute("SELECT status FROM app.users WHERE id = %s", (person,)).fetchone()["status"] == "rejected"
    assert sql.execute("SELECT count(*) AS n FROM app.audit_events").fetchone()["n"] == before  # nothing audited


async def test_expect_status_matching_behaves_as_before_and_omitting_it_changes_nothing(login, sql):
    a = add_user(sql, "a@example.org", "pending")
    b = add_user(sql, "b@example.org", "pending")
    admin = await login(ADMIN, role="admin")
    ok = await admin.post(f"/v1/admin/users/{a}/approve?expect_status=pending", json={"role": "builder"})
    assert ok.status_code == 200 and (ok.json()["status"], ok.json()["role"]) == ("active", "builder")
    plain = await admin.post(f"/v1/admin/users/{b}/approve", json={"role": "user"})  # old callers: no parameter
    assert plain.status_code == 200
    # without the guard an already active person is simply re-approved (feature 002 behaviour, unchanged)
    again = await admin.post(f"/v1/admin/users/{b}/approve", json={"role": "builder"})
    assert again.status_code == 200 and again.json()["role"] == "builder"


async def test_expect_status_on_suspend_and_role_and_unknown_values(login, sql):
    person = add_user(sql, "p@example.org", "active", role="user")
    admin = await login(ADMIN, role="admin")
    assert (await admin.post(f"/v1/admin/users/{person}/role?expect_status=pending", json={"role": "admin"})).status_code == 409
    assert (await admin.post(f"/v1/admin/users/{person}/suspend?expect_status=pending")).status_code == 409
    assert (await admin.post(f"/v1/admin/users/{person}/role?expect_status=active", json={"role": "builder"})).status_code == 200
    assert (await admin.post(f"/v1/admin/users/{person}/suspend?expect_status=bogus")).status_code == 422
    assert (await admin.post(f"/v1/admin/users/{person}/suspend?expect_status=active")).status_code == 200


async def test_two_admins_approving_at_once_get_one_success_and_one_conflict(login, sql):
    person = add_user(sql, "p@example.org", "pending")
    one = await login("one@example.com", role="admin")
    two = await login("two@example.com", role="admin")
    results = await asyncio.gather(
        one.post(f"/v1/admin/users/{person}/approve?expect_status=pending", json={"role": "user"}),
        two.post(f"/v1/admin/users/{person}/approve?expect_status=pending", json={"role": "builder"}),
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    approved = sql.execute("SELECT count(*) AS n FROM app.audit_events WHERE action = 'user_approved'").fetchone()["n"]
    assert approved == 1
