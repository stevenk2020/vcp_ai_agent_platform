"""Request helpers: client address, current user from the session cookie, role guard."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Request

from app.errors import forbidden, unauthenticated
from app.security import hash_token

COOKIE_NAME = "__Host-session"


def client_ip(request: Request) -> str | None:
    """The caller's address: the first X-Forwarded-For value (set by Caddy) if it is a valid address,
    otherwise the socket peer."""
    forwarded = request.headers.get("x-forwarded-for", "")
    candidates = []
    if forwarded:
        candidates.append(forwarded.split(",")[0].strip())
    if request.client:
        candidates.append(request.client.host)
    for candidate in candidates:
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    email: str
    role: str
    session_id: UUID


@dataclass(frozen=True)
class CurrentPerson:
    """A signed-in person who may still be waiting for approval (status pending). `role` only means
    something once the person is active."""

    id: UUID
    email: str
    role: str
    status: str
    session_id: UUID


async def current_user(request: Request) -> CurrentUser:
    """The signed-in, APPROVED user (status active). The session is re-checked against the database on
    every request, so revocation, expiry, suspension and role changes apply immediately.

    Do NOT relax this to accept pending people: a waiting session must open only the routes that
    explicitly use `current_person` (own status, access request). Every other route stays protected
    by this function, and tests/integration/test_deny_by_default.py enforces it for every route."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise unauthenticated()
    state = request.app.state
    async with state.db.pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT s.id AS session_id, u.id, u.email, u.role "
            "FROM app.sessions s JOIN app.users u ON u.id = s.user_id "
            "WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > %s AND u.status = 'active'",
            (hash_token(token), state.clock.now()),
        )
        row = await cursor.fetchone()
    if row is None:
        raise unauthenticated()
    return CurrentUser(id=row["id"], email=row["email"], role=row["role"], session_id=row["session_id"])


async def current_person(request: Request) -> CurrentPerson:
    """Like `current_user` but also accepts a person who is still pending (a waiting session).
    Suspended and rejected people are never accepted. Use only for routes meant for waiting people."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise unauthenticated()
    state = request.app.state
    async with state.db.pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT s.id AS session_id, u.id, u.email, u.role, u.status "
            "FROM app.sessions s JOIN app.users u ON u.id = s.user_id "
            "WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > %s "
            "AND u.status IN ('active', 'pending')",
            (hash_token(token), state.clock.now()),
        )
        row = await cursor.fetchone()
    if row is None:
        raise unauthenticated()
    return CurrentPerson(
        id=row["id"], email=row["email"], role=row["role"], status=row["status"], session_id=row["session_id"]
    )


def require_role(*roles: str):
    async def dependency(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        if user.role not in roles:
            raise forbidden()
        return user

    return dependency
