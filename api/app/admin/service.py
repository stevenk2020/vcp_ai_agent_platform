"""Admin actions: allowlist, approval queue, status and role changes, with immediate revocation."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg.errors

from app import audit
from app.auth.deps import CurrentUser
from app.errors import ApiError, conflict, last_admin, not_found
from app.security import InvalidEmail, normalize_email

ROLES = ("admin", "builder", "user")
STATUSES = ("pending", "active", "suspended", "rejected")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
ADMIN_LOCK = "admins"


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _entry(row) -> dict:
    return {
        "id": str(row["id"]), "kind": row["kind"], "value": row["value"], "default_role": row["default_role"],
        "added_by": str(row["added_by"]) if row["added_by"] else None, "created_at": _iso(row["created_at"]),
    }


def _user(row) -> dict:
    return {
        "id": str(row["id"]), "email": row["email"], "role": row["role"], "status": row["status"],
        "created_at": _iso(row["created_at"]), "last_login_at": _iso(row["last_login_at"]),
        "name": row["name"], "request_reason": row["request_reason"],
        "request_submitted_at": _iso(row["request_submitted_at"]),
    }


def _invalid(message: str) -> ApiError:
    return ApiError(422, "invalid_request", message)


async def _lock_admins(conn) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (ADMIN_LOCK,))


async def _other_active_admins(conn, excluding: list) -> int:
    cursor = await conn.execute(
        "SELECT count(*) AS n FROM app.users WHERE role = 'admin' AND status = 'active' AND NOT (id = ANY(%s))",
        (excluding,),
    )
    return (await cursor.fetchone())["n"]


async def _revoke_sessions(conn, state, user_id, reason: str) -> int:
    now = state.clock.now()
    cursor = await conn.execute(
        "UPDATE app.sessions SET revoked_at = %s, revoked_reason = %s, updated_at = %s "
        "WHERE user_id = %s AND revoked_at IS NULL",
        (now, reason, now, user_id),
    )
    return cursor.rowcount


# ---- allowlist ---------------------------------------------------------------------------------

async def add_entry(state, actor: CurrentUser, kind: str, value: str, role: str, ip: str | None) -> dict:
    if kind == "email":
        try:
            value = normalize_email(value)
        except InvalidEmail as exc:
            raise _invalid("The email address is not valid.") from exc
    else:
        value = value.strip().lower()
        if not DOMAIN_RE.match(value):
            raise _invalid("The domain is not valid.")
    clock = state.clock
    try:
        async with state.db.transaction() as conn:
            cursor = await conn.execute(
                "INSERT INTO app.allowlist (kind, value, default_role, added_by) VALUES (%s, %s, %s, %s) "
                "RETURNING id, kind, value, default_role, added_by, created_at",
                (kind, value, role, actor.id),
            )
            row = await cursor.fetchone()
            await audit.record(conn, clock, "allowlist_added", actor_user_id=actor.id, actor_email=actor.email,
                               target=f"{kind}:{value}", ip=ip, detail={"default_role": role})
    except psycopg.errors.UniqueViolation as exc:
        raise conflict("This entry already exists.") from exc
    return _entry(row)


async def list_entries(state) -> list[dict]:
    async with state.db.pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT id, kind, value, default_role, added_by, created_at FROM app.allowlist ORDER BY created_at, id"
        )
        return [_entry(r) for r in await cursor.fetchall()]


async def remove_entry(state, actor: CurrentUser, entry_id: UUID, ip: str | None) -> None:
    """Delete an entry; users who matched only this entry are suspended and their sessions revoked,
    unless that would leave no active admin (then nothing changes and the attempt is audited)."""
    clock = state.clock
    blocked = False
    async with state.db.transaction() as conn:
        await _lock_admins(conn)
        cursor = await conn.execute("SELECT id, kind, value FROM app.allowlist WHERE id = %s FOR UPDATE", (entry_id,))
        entry = await cursor.fetchone()
        if entry is None:
            raise not_found("Allowlist entry not found.")

        if entry["kind"] == "email":
            match_sql, match_param = "u.email = %s", entry["value"]
        else:
            match_sql, match_param = "split_part(u.email, '@', 2) = %s", entry["value"]
        cursor = await conn.execute(
            f"SELECT u.id, u.email, u.role FROM app.users u WHERE u.status = 'active' AND {match_sql} "
            "AND NOT EXISTS (SELECT 1 FROM app.allowlist a WHERE a.id <> %s AND "
            "((a.kind = 'email' AND a.value = u.email) OR (a.kind = 'domain' AND a.value = split_part(u.email, '@', 2))))",
            (match_param, entry_id),
        )
        affected = await cursor.fetchall()
        admins = [r["id"] for r in affected if r["role"] == "admin"]
        if admins and await _other_active_admins(conn, admins) == 0:
            blocked = True
            await audit.record(conn, clock, "last_admin_blocked", "failure", actor_user_id=actor.id,
                               actor_email=actor.email, target=f"{entry['kind']}:{entry['value']}", ip=ip)
        else:
            await conn.execute("DELETE FROM app.allowlist WHERE id = %s", (entry_id,))
            await audit.record(conn, clock, "allowlist_removed", actor_user_id=actor.id, actor_email=actor.email,
                               target=f"{entry['kind']}:{entry['value']}", ip=ip)
            now = clock.now()
            for person in affected:
                await conn.execute(
                    "UPDATE app.users SET status = 'suspended', suspended_reason = 'allowlist_removed', "
                    "updated_at = %s WHERE id = %s",
                    (now, person["id"]),
                )
                revoked = await _revoke_sessions(conn, state, person["id"], "allowlist_removed")
                if revoked:
                    await audit.record(conn, clock, "session_revoked", actor_user_id=actor.id,
                                       actor_email=actor.email, target=person["email"], ip=ip,
                                       detail={"reason": "allowlist_removed", "sessions": revoked})
    if blocked:
        raise last_admin()


# ---- people ------------------------------------------------------------------------------------

async def list_users(state, status: str | None) -> list[dict]:
    if status is not None and status not in STATUSES:
        raise _invalid("Unknown status.")
    sql = "SELECT id, email, role, status, created_at, last_login_at, name, request_reason, request_submitted_at FROM app.users"
    params: tuple = ()
    if status:
        sql += " WHERE status = %s"
        params = (status,)
    async with state.db.pool.connection() as conn:
        cursor = await conn.execute(sql + " ORDER BY created_at, email", params)
        return [_user(r) for r in await cursor.fetchall()]


async def _change_user(state, actor: CurrentUser, user_id: UUID, ip: str | None, *, new_status: str | None,
                       new_role: str | None, reason: str | None, audit_action: str, revoke_reason: str | None) -> dict:
    """Shared path for approve, reject, suspend and role change, with last-admin protection."""
    clock = state.clock
    blocked = False
    async with state.db.transaction() as conn:
        await _lock_admins(conn)
        cursor = await conn.execute(
            "SELECT id, email, role, status, created_at, last_login_at, name, request_reason, request_submitted_at "
            "FROM app.users WHERE id = %s FOR UPDATE",
            (user_id,),
        )
        user = await cursor.fetchone()
        if user is None:
            raise not_found("User not found.")
        status = new_status or user["status"]
        role = new_role or user["role"]

        loses_admin = user["status"] == "active" and user["role"] == "admin" and (status != "active" or role != "admin")
        if loses_admin and await _other_active_admins(conn, [user["id"]]) == 0:
            blocked = True
            await audit.record(conn, clock, "last_admin_blocked", "failure", actor_user_id=actor.id,
                               actor_email=actor.email, target=user["email"], ip=ip)
        else:
            now = clock.now()
            suspended_reason = reason if status == "suspended" else None
            cursor = await conn.execute(
                "UPDATE app.users SET status = %s, role = %s, suspended_reason = %s, updated_at = %s WHERE id = %s "
                "RETURNING id, email, role, status, created_at, last_login_at, name, request_reason, request_submitted_at",
                (status, role, suspended_reason, now, user_id),
            )
            updated = await cursor.fetchone()
            detail = {"from": user["role"], "to": role} if audit_action == "role_changed" else {"role": role}
            await audit.record(conn, clock, audit_action, actor_user_id=actor.id, actor_email=actor.email,
                               target=user["email"], ip=ip, detail=detail)
            if revoke_reason:
                revoked = await _revoke_sessions(conn, state, user_id, revoke_reason)
                if revoked:
                    await audit.record(conn, clock, "session_revoked", actor_user_id=actor.id,
                                       actor_email=actor.email, target=user["email"], ip=ip,
                                       detail={"reason": revoke_reason, "sessions": revoked})
    if blocked:
        raise last_admin()
    return _user(updated)


async def approve(state, actor, user_id, role: str, ip):
    return await _change_user(state, actor, user_id, ip, new_status="active", new_role=role, reason=None,
                              audit_action="user_approved", revoke_reason=None)


async def reject(state, actor, user_id, ip):
    return await _change_user(state, actor, user_id, ip, new_status="rejected", new_role=None, reason=None,
                              audit_action="user_rejected", revoke_reason="rejected")


async def suspend(state, actor, user_id, ip):
    return await _change_user(state, actor, user_id, ip, new_status="suspended", new_role=None, reason="admin",
                              audit_action="user_suspended", revoke_reason="suspended")


async def set_role(state, actor, user_id, role: str, ip):
    return await _change_user(state, actor, user_id, ip, new_status=None, new_role=role, reason=None,
                              audit_action="role_changed", revoke_reason=None)


# ---- audit trail (read only) -------------------------------------------------------------------

async def read_audit(state, limit: int, action: str | None, before: int | None) -> list[dict]:
    if action is not None and action not in audit.ACTIONS:
        raise _invalid("Unknown action.")
    clauses, params = [], []
    if action:
        clauses.append("action = %s")
        params.append(action)
    if before:
        clauses.append("id < %s")
        params.append(before)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    async with state.db.pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT id, at, actor_user_id, actor_email, action, target, outcome, host(ip) AS ip, detail "
            f"FROM app.audit_events{where} ORDER BY id DESC LIMIT %s",
            (*params, limit),
        )
        rows = await cursor.fetchall()
    return [
        {
            "id": r["id"], "at": _iso(r["at"]),
            "actor_user_id": str(r["actor_user_id"]) if r["actor_user_id"] else None,
            "actor_email": r["actor_email"], "action": r["action"], "target": r["target"],
            "outcome": r["outcome"], "ip": r["ip"], "detail": r["detail"],
        }
        for r in rows
    ]
