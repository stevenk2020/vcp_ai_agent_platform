"""Deny by default: a waiting session (a pending person's session) opens ONLY the routes listed below.

The test walks every registered route, so a route added later must either be in the allow-lists or refuse a waiting
session; otherwise this test fails and names the route (spec 003 FR-006, constitution v1.0.1)."""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.routing import APIRoute

from tests.integration.helpers import add_allowlist, add_user

# what a waiting session may call (own status, access request, sign-out) and what is open to everybody
WAITING_ALLOWED = {
    ("GET", "/v1/access"), ("POST", "/v1/access/request"), ("POST", "/v1/auth/logout"),
}
PUBLIC = {
    ("GET", "/v1/health"), ("POST", "/v1/auth/code/request"), ("POST", "/v1/auth/code/verify"),
    ("GET", "/v1/auth/google/start"), ("GET", "/v1/auth/google/callback"),
    ("GET", "/"), ("GET", "/static/{name:path}"),
}
# routes that must exist (they are part of the contract); a missing one is a failure, not a skip
MUST_EXIST = WAITING_ALLOWED | PUBLIC
# page routes for admins: refusal is 302 (signed out) or 403 (not an admin), never 200
ADMIN_PAGES = {("GET", "/admin"), ("GET", "/admin/assets/{name}")}


def routes(app):
    """Every registered (method, path) -> route, including routers added with include_router (which newer FastAPI
    keeps as lazily-resolved wrappers, so they are walked recursively through their original router)."""
    found = {}

    def walk(items, prefix=""):
        for route in items:
            if isinstance(route, APIRoute):
                for method in route.methods - {"HEAD", "OPTIONS"}:
                    found[(method, prefix + route.path)] = route
            elif hasattr(route, "original_router"):
                walk(route.original_router.routes, prefix + route.include_context.prefix)
            else:
                raise AssertionError(f"unknown route type {type(route).__name__}: the route walk must understand it")

    walk(app.routes)
    return found


def fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", lambda m: "admin.js" if "name" in m.group(0) else str(uuid.uuid4()), path)


async def call(client, method, path):
    kwargs = {"json": {}} if method not in ("GET", "DELETE") else {}
    return await client.request(method, path, follow_redirects=False, **kwargs)


async def test_a_waiting_session_opens_nothing_but_its_own_routes(app, login_pending, new_client, login, sql):
    waiting = await login_pending("walker@example.org")
    anonymous = new_client()
    plain = await login("plain@example.com", role="user")
    exercised = set()

    for (method, route_path), _route in sorted(routes(app).items()):
        key = (method, route_path)
        exercised.add(key)
        if key in WAITING_ALLOWED or key in PUBLIC:
            continue
        path = fill(route_path)
        if key in ADMIN_PAGES:
            for client in (waiting, anonymous, plain):
                response = await call(client, method, path)
                assert response.status_code in (302, 403, 404), f"{method} {route_path}: {response.status_code}"
            continue
        denied = await call(waiting, method, path)
        nothing = await call(anonymous, method, path)
        assert denied.status_code == 401, f"{method} {route_path} accepted a waiting session ({denied.status_code})"
        assert nothing.status_code == 401, f"{method} {route_path} accepted no session ({nothing.status_code})"
        assert denied.json() == nothing.json(), f"{method} {route_path}: waiting and anonymous refusals differ"
        if route_path.startswith("/v1/admin"):
            assert (await call(plain, method, path)).status_code == 403, f"{method} {route_path}: non-admin not refused"

    registered = set(routes(app))
    assert exercised == registered
    # the allow-list never names a route that does not exist, and the contract routes are all present
    for key in WAITING_ALLOWED | PUBLIC | ADMIN_PAGES:
        if key in registered:
            continue
        assert key in PENDING_LATER, f"allow-list names a route that is not registered: {key}; registered={sorted(registered)}"
    for key in MUST_EXIST:
        assert key in registered, f"contract route missing: {key}"


# routes that arrive in later phases of this feature; once registered the test checks them like any other.
# The access routes and the start page are mandatory since US1/US2; T024 (US3) adds the admin pages.
PENDING_LATER = {("GET", "/admin"), ("GET", "/admin/assets/{name}")}


async def test_the_waiting_session_still_works_for_logout_and_is_revoked_like_any_session(login_pending, sql):
    waiting = await login_pending("walker@example.org")
    assert (await waiting.post("/v1/auth/logout")).status_code == 204
    # after sign-out the cookie no longer identifies anyone
    assert sql.execute("SELECT count(*) AS n FROM app.sessions WHERE revoked_at IS NULL").fetchone()["n"] == 0


async def test_rejecting_or_suspending_ends_a_waiting_session_immediately(login_pending, login, sql):
    admin = await login("admin@example.com", role="admin")
    for action, email in (("reject", "r@example.org"), ("suspend", "s@example.org")):
        waiting = await login_pending(email)
        person = sql.execute("SELECT id FROM app.users WHERE email = %s", (email,)).fetchone()["id"]
        assert (await admin.post(f"/v1/admin/users/{person}/{action}")).status_code == 200
        revoked = sql.execute(
            "SELECT count(*) AS n FROM app.sessions WHERE user_id = %s AND revoked_at IS NULL", (person,)
        ).fetchone()["n"]
        assert revoked == 0, action


async def test_a_waiting_session_can_call_exactly_its_own_routes(login_pending, sql):
    waiting = await login_pending("walker@example.org")
    assert (await waiting.get("/v1/access")).status_code == 200
    assert (await waiting.post("/v1/access/request", json={"name": "W"})).status_code == 201
    assert (await waiting.post("/v1/auth/logout")).status_code == 204
    assert (await waiting.get("/v1/access")).status_code == 401  # sign-out ended it
