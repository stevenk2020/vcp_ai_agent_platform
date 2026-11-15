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


# ---- allowlist and people (US4) ----------------------------------------------------------------------

async def test_allowlist_items_say_who_added_them(login, sql):
    one = await login("one@example.com", role="admin")
    created = await one.post("/v1/admin/allowlist", json={"kind": "email", "value": "n@example.org", "default_role": "user"})
    assert created.status_code == 201 and created.json()["added_by_email"] == "one@example.com"
    items = (await one.get("/v1/admin/allowlist")).json()["items"]
    entry = next(i for i in items if i["value"] == "n@example.org")
    assert entry["added_by_email"] == "one@example.com"
    assert set(entry) >= {"id", "kind", "value", "default_role", "added_by", "created_at", "added_by_email"}
    # an entry whose adder row is gone (or was a script) has no adder email but is still listed
    sql.execute("INSERT INTO app.allowlist (kind, value, default_role) VALUES ('domain', 'corp.example', 'user')")
    items = (await one.get("/v1/admin/allowlist")).json()["items"]
    assert next(i for i in items if i["value"] == "corp.example")["added_by_email"] is None


@pytest.mark.parametrize("status,reason", [("suspended", "admin"), ("rejected", None)])
async def test_reactivation_is_approve_with_a_role_and_old_sessions_stay_dead(login, new_client, sql, status, reason):
    person = add_user(sql, "back@example.org", status, reason=reason)
    admin = await login(ADMIN, role="admin")
    response = await admin.post(f"/v1/admin/users/{person}/approve?expect_status={status}", json={"role": "builder"})
    assert response.status_code == 200 and (response.json()["status"], response.json()["role"]) == ("active", "builder")
    row = sql.execute("SELECT suspended_reason FROM app.users WHERE id = %s", (person,)).fetchone()
    assert row["suspended_reason"] is None
    assert sql.execute("SELECT count(*) AS n FROM app.audit_events WHERE action = 'user_approved'").fetchone()["n"] == 1
    assert sql.execute("SELECT count(*) AS n FROM app.sessions WHERE user_id = %s AND revoked_at IS NULL",
                       (person,)).fetchone()["n"] == 0


async def test_the_last_admin_is_protected_with_a_stable_error_the_page_maps(login, sql):
    only_admin = await login(ADMIN, role="admin")
    me = (await only_admin.get("/v1/me")).json()["id"]
    for path, body in ((f"/v1/admin/users/{me}/suspend", None), (f"/v1/admin/users/{me}/role", {"role": "user"})):
        response = await only_admin.post(path, json=body)
        assert response.status_code == 409 and response.json()["error"] == "last_admin", path
    entry = sql.execute("SELECT id FROM app.allowlist WHERE value = %s", (ADMIN,)).fetchone()["id"]  # added by login()
    response = await only_admin.delete(f"/v1/admin/allowlist/{entry}")
    assert response.status_code == 409 and response.json()["error"] == "last_admin"
    assert (await only_admin.get("/v1/me")).status_code == 200


async def test_bad_or_duplicate_allowlist_entries_explain_themselves(login):
    admin = await login(ADMIN, role="admin")
    bad_email = await admin.post("/v1/admin/allowlist", json={"kind": "email", "value": "not-an-email", "default_role": "user"})
    assert bad_email.status_code == 422 and bad_email.json()["message"] == "The email address is not valid."
    bad_domain = await admin.post("/v1/admin/allowlist", json={"kind": "domain", "value": "no_dot", "default_role": "user"})
    assert bad_domain.status_code == 422 and bad_domain.json()["message"] == "The domain is not valid."
    body = {"kind": "domain", "value": "corp.example", "default_role": "user"}
    assert (await admin.post("/v1/admin/allowlist", json=body)).status_code == 201
    duplicate = await admin.post("/v1/admin/allowlist", json=body)
    assert duplicate.status_code == 409 and duplicate.json()["error"] == "conflict"


async def test_removing_an_entry_ends_that_persons_session_on_the_next_request(login):
    admin = await login(ADMIN, role="admin")
    person = await login("gone@example.org")
    entries = (await admin.get("/v1/admin/allowlist")).json()["items"]
    entry = next(i for i in entries if i["value"] == "gone@example.org")
    assert (await person.get("/v1/me")).status_code == 200
    assert (await admin.delete(f"/v1/admin/allowlist/{entry['id']}")).status_code == 204
    assert (await person.get("/v1/me")).status_code == 401
