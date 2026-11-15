"""US2: the admin API: allowlist, approval queue, role and status changes, immediate revocation."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.integration.helpers import add_allowlist, add_user

ADMIN = "admin@example.com"
ALICE = "alice@example.com"


async def add_entry(admin, value, kind="email", role="user"):
    return await admin.post("/v1/admin/allowlist", json={"kind": kind, "value": value, "default_role": role})


# ---- allowlist ---------------------------------------------------------------------------------

async def test_admin_adds_lists_and_sees_who_added_each_entry(login):
    admin = await login(ADMIN, role="admin")
    me = (await admin.get("/v1/me")).json()
    created = await add_entry(admin, "Bob@Example.COM", role="builder")
    assert created.status_code == 201
    entry = created.json()
    assert (entry["kind"], entry["value"], entry["default_role"]) == ("email", "bob@example.com", "builder")
    assert entry["added_by"] == me["id"] and entry["created_at"] and entry["id"]

    listing = (await admin.get("/v1/admin/allowlist")).json()["items"]
    mine = [i for i in listing if i["value"] == "bob@example.com"][0]
    assert mine["added_by"] == me["id"] and mine["created_at"]


async def test_domain_entries_duplicates_and_malformed_values(login):
    admin = await login(ADMIN, role="admin")
    assert (await add_entry(admin, "Example.ORG", kind="domain")).status_code == 201
    duplicate = await add_entry(admin, "example.org", kind="domain")
    assert duplicate.status_code == 409 and duplicate.json()["error"] == "conflict"
    for kind, value in (("email", "not-an-email"), ("domain", "has@sign.com"), ("domain", "no spaces.com"), ("domain", "")):
        assert (await add_entry(admin, value, kind=kind)).status_code == 422, (kind, value)
    assert (await admin.post("/v1/admin/allowlist", json={"kind": "email", "value": "a@b.com", "default_role": "root"})).status_code == 422


async def test_an_email_entry_beats_a_domain_entry_and_subdomains_do_not_match(login, new_client, mailer, sql):
    add_allowlist(sql, "example.com", kind="domain", role="user")
    add_allowlist(sql, "carol@example.com", kind="email", role="builder")
    carol = await login("carol@example.com", entry=False)
    assert (await carol.get("/v1/me")).json()["role"] == "builder"
    other = await login("dave@example.com", entry=False)
    assert (await other.get("/v1/me")).json()["role"] == "user"

    from tests.integration.helpers import read_code

    stranger = new_client()
    await stranger.post("/v1/auth/code/request", json={"email": "eve@sub.example.com"})
    response = await stranger.post(
        "/v1/auth/code/verify", json={"email": "eve@sub.example.com", "code": read_code(mailer, "eve@sub.example.com")}
    )
    assert response.json()["status"] != "signed_in"
    assert (await stranger.get("/v1/me")).status_code == 401


# ---- who may call admin actions ----------------------------------------------------------------

ADMIN_CALLS = [
    ("GET", "/v1/admin/allowlist", None),
    ("POST", "/v1/admin/allowlist", {"kind": "email", "value": "x@example.com", "default_role": "user"}),
    ("DELETE", f"/v1/admin/allowlist/{uuid.uuid4()}", None),
    ("GET", "/v1/admin/users", None),
    ("POST", f"/v1/admin/users/{uuid.uuid4()}/approve", {"role": "user"}),
    ("POST", f"/v1/admin/users/{uuid.uuid4()}/reject", None),
    ("POST", f"/v1/admin/users/{uuid.uuid4()}/suspend", None),
    ("POST", f"/v1/admin/users/{uuid.uuid4()}/role", {"role": "user"}),
]


@pytest.mark.parametrize("method,path,body", ADMIN_CALLS)
async def test_non_admins_get_403_and_anonymous_callers_get_401(login, new_client, method, path, body):
    for role in ("user", "builder"):
        person = await login(f"{role}-person@example.com", role=role)
        response = await person.request(method, path, json=body)
        assert response.status_code == 403 and response.json()["error"] == "forbidden", (role, response.text)
    anonymous = new_client()
    response = await anonymous.request(method, path, json=body)
    assert response.status_code == 401 and response.json()["error"] == "unauthenticated"


async def test_admin_actions_need_the_public_origin(login, new_client):
    admin = await login(ADMIN, role="admin")
    session_cookie = admin.cookies.get("__Host-session")
    no_origin = new_client(origin=None)
    no_origin.cookies.set("__Host-session", session_cookie)
    response = await no_origin.post(
        "/v1/admin/allowlist", json={"kind": "email", "value": "x@example.com", "default_role": "user"}
    )
    assert response.status_code == 403 and response.json()["error"] == "origin_not_allowed"


# ---- removal cuts access at once ---------------------------------------------------------------

async def test_removing_an_entry_revokes_sessions_on_the_next_request(login, sql):
    admin = await login(ADMIN, role="admin")
    entry = (await add_entry(admin, ALICE)).json()
    alice = await login(ALICE, entry=False)
    assert (await alice.get("/v1/me")).status_code == 200

    removal = await admin.delete(f"/v1/admin/allowlist/{entry['id']}")
    assert removal.status_code == 204
    assert (await alice.get("/v1/me")).status_code == 401

    user = sql.execute("SELECT status, suspended_reason FROM app.users WHERE email = %s", (ALICE,)).fetchone()
    assert (user["status"], user["suspended_reason"]) == ("suspended", "allowlist_removed")
    session = sql.execute("SELECT revoked_at, revoked_reason FROM app.sessions s JOIN app.users u ON u.id = s.user_id "
                          "WHERE u.email = %s", (ALICE,)).fetchone()
    assert session["revoked_at"] is not None and session["revoked_reason"] == "allowlist_removed"
    actions = {r["action"] for r in sql.execute("SELECT action FROM app.audit_events").fetchall()}
    assert {"allowlist_added", "allowlist_removed", "session_revoked"} <= actions


async def test_a_person_matched_by_another_entry_keeps_working(login):
    admin = await login(ADMIN, role="admin")
    domain = (await add_entry(admin, "example.com", kind="domain")).json()
    await add_entry(admin, ALICE)
    alice = await login(ALICE, entry=False)
    assert (await admin.delete(f"/v1/admin/allowlist/{domain['id']}")).status_code == 204
    assert (await alice.get("/v1/me")).status_code == 200


async def test_deleting_an_unknown_entry_is_404(login):
    admin = await login(ADMIN, role="admin")
    assert (await admin.delete(f"/v1/admin/allowlist/{uuid.uuid4()}")).status_code == 404


# ---- approval queue, status and role ------------------------------------------------------------

async def test_the_queue_lists_pending_people_and_filters_by_status(login, sql):
    admin = await login(ADMIN, role="admin")
    add_user(sql, "p1@example.com", "pending")
    add_user(sql, "r1@example.com", "rejected")
    pending = (await admin.get("/v1/admin/users?status=pending")).json()["items"]
    assert [u["email"] for u in pending] == ["p1@example.com"]
    assert set(pending[0]) >= {"id", "email", "role", "status", "created_at", "last_login_at"}
    everyone = {u["email"] for u in (await admin.get("/v1/admin/users")).json()["items"]}
    assert {"p1@example.com", "r1@example.com", ADMIN} <= everyone
    assert (await admin.get("/v1/admin/users?status=banned")).status_code == 422


async def test_approve_reject_suspend_and_change_role(login, sql):
    admin = await login(ADMIN, role="admin")
    pending_id = add_user(sql, "p@example.com", "pending")

    approved = await admin.post(f"/v1/admin/users/{pending_id}/approve", json={"role": "builder"})
    assert approved.status_code == 200
    assert (approved.json()["status"], approved.json()["role"]) == ("active", "builder")

    other_id = add_user(sql, "q@example.com", "pending")
    rejected = await admin.post(f"/v1/admin/users/{other_id}/reject")
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"

    alice = await login(ALICE)
    alice_id = (await alice.get("/v1/me")).json()["id"]
    suspended = await admin.post(f"/v1/admin/users/{alice_id}/suspend")
    assert suspended.status_code == 200 and suspended.json()["status"] == "suspended"
    assert (await alice.get("/v1/me")).status_code == 401
    reason = sql.execute("SELECT suspended_reason FROM app.users WHERE id = %s", (alice_id,)).fetchone()
    assert reason["suspended_reason"] == "admin"

    again = await admin.post(f"/v1/admin/users/{alice_id}/approve", json={"role": "user"})
    assert again.json()["status"] == "active"
    assert sql.execute("SELECT suspended_reason FROM app.users WHERE id = %s", (alice_id,)).fetchone()["suspended_reason"] is None

    assert (await admin.post(f"/v1/admin/users/{uuid.uuid4()}/approve", json={"role": "user"})).status_code == 404


async def test_a_role_change_applies_on_the_next_request(login, sql):
    admin = await login(ADMIN, role="admin")
    alice = await login(ALICE, role="user")
    alice_id = (await alice.get("/v1/me")).json()["id"]
    assert (await alice.get("/v1/admin/users")).status_code == 403
    changed = await admin.post(f"/v1/admin/users/{alice_id}/role", json={"role": "admin"})
    assert changed.status_code == 200 and changed.json()["role"] == "admin"
    assert (await alice.get("/v1/me")).json()["role"] == "admin"
    assert (await alice.get("/v1/admin/users")).status_code == 200
    detail = sql.execute("SELECT detail FROM app.audit_events WHERE action = 'role_changed'").fetchone()["detail"]
    assert detail == {"from": "user", "to": "admin"}


# ---- the last admin ----------------------------------------------------------------------------

async def test_the_last_admin_cannot_be_removed_suspended_rejected_or_demoted(login, sql):
    entry_admin = await login(ADMIN, role="admin")
    admin_id = (await entry_admin.get("/v1/me")).json()["id"]
    entries = (await entry_admin.get("/v1/admin/allowlist")).json()["items"]
    entry_id = [e for e in entries if e["value"] == ADMIN][0]["id"]

    for call in (
        entry_admin.delete(f"/v1/admin/allowlist/{entry_id}"),
        entry_admin.post(f"/v1/admin/users/{admin_id}/suspend"),
        entry_admin.post(f"/v1/admin/users/{admin_id}/reject"),
        entry_admin.post(f"/v1/admin/users/{admin_id}/role", json={"role": "user"}),
    ):
        response = await call
        assert response.status_code == 409 and response.json()["error"] == "last_admin"

    assert (await entry_admin.get("/v1/me")).status_code == 200
    assert sql.execute("SELECT count(*) AS n FROM app.allowlist WHERE value = %s", (ADMIN,)).fetchone()["n"] == 1
    assert "last_admin_blocked" in {r["action"] for r in sql.execute("SELECT action FROM app.audit_events").fetchall()}


async def test_with_two_admins_one_can_go_but_not_the_other(login, sql):
    first = await login(ADMIN, role="admin")
    second = await login("admin2@example.com", role="admin")
    second_id = (await second.get("/v1/me")).json()["id"]
    first_id = (await first.get("/v1/me")).json()["id"]
    assert (await first.post(f"/v1/admin/users/{second_id}/suspend")).status_code == 200
    blocked = await first.post(f"/v1/admin/users/{first_id}/suspend")
    assert blocked.status_code == 409 and blocked.json()["error"] == "last_admin"


async def test_two_admins_suspending_each_other_at_once_leave_one(login, sql):
    first = await login(ADMIN, role="admin")
    second = await login("admin2@example.com", role="admin")
    first_id = (await first.get("/v1/me")).json()["id"]
    second_id = (await second.get("/v1/me")).json()["id"]
    results = await asyncio.gather(
        first.post(f"/v1/admin/users/{second_id}/suspend"),
        second.post(f"/v1/admin/users/{first_id}/suspend"),
    )
    assert sorted(r.status_code for r in results).count(200) == 1
    remaining = sql.execute("SELECT count(*) AS n FROM app.users WHERE role = 'admin' AND status = 'active'").fetchone()["n"]
    assert remaining == 1
