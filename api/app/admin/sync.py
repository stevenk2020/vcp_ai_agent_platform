"""Admin endpoints for the sync worker (specs/004-sales-contract-sync, contracts/http-api.md).

They only read and write the small control tables in schema serving through the API's own role; the API never
opens a connection to SQL Server or to the worker.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from psycopg import errors as pg_errors

from app import audit, errors
from app.auth.deps import CurrentUser, client_ip, require_role

router = APIRouter(prefix="/v1/admin/sync")
admin_only = require_role("admin")


def derive_state(row: dict, now: datetime) -> dict:
    """The rules of data-model.md; the same cases are tested in the worker (sync/tests/state_cases.json)."""
    as_of = row.get("data_as_of")
    age = None if as_of is None else max(0, int((now - as_of).total_seconds()))
    if as_of is None:
        state = "no_data"
    elif row["consecutive_failures"] >= row["failing_after"]:
        state = "failing"
    elif age > row["stale_after_seconds"]:
        state = "stale"
    else:
        state = "healthy"
    return {"state": state, "age_seconds": age}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _status_row(row: dict, now: datetime) -> dict:
    derived = derive_state(row, now)
    return {
        "table": row["table_name"],
        "state": derived["state"],
        "data_as_of": _iso(row["data_as_of"]),
        "age_seconds": derived["age_seconds"],
        "source_rows": row["source_rows"],
        "copied_rows": row["copied_rows"],
        "consecutive_failures": row["consecutive_failures"],
        "last_outcome": row["last_outcome"],
        "last_reason": row["last_reason"],
        "running": bool(row["running"]),
        "request_pending": bool(row["request_pending"]),
        "stale_after_seconds": row["stale_after_seconds"],
        "failing_after": row["failing_after"],
    }


def _run_row(row: dict) -> dict:
    return {
        "id": row["id"], "table": row["table_name"], "trigger": row["trigger"],
        "started_at": _iso(row["started_at"]), "finished_at": _iso(row["finished_at"]), "duration_ms": row["duration_ms"],
        "outcome": row["outcome"], "reason_code": row["reason_code"], "source_rows": row["source_rows"],
        "copied_rows": row["copied_rows"], "error_text": row["error_text"], "warnings": row["warnings"],
    }


@router.get("")
async def sync_status(request: Request, _: CurrentUser = Depends(admin_only)):
    state = request.app.state
    now = state.clock.now()
    ttl_start = now - timedelta(minutes=state.settings.sync_request_ttl_minutes)
    try:
        async with state.db.pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT s.*, "
                "COALESCE((SELECT r.outcome = 'running' FROM serving.sync_runs r WHERE r.table_name = s.table_name "
                "          ORDER BY r.id DESC LIMIT 1), false) AS running, "
                "EXISTS (SELECT 1 FROM serving.sync_requests q WHERE q.table_name = s.table_name AND q.state = 'pending' "
                "        AND q.requested_at > %s) AS request_pending "
                "FROM serving.sync_state s ORDER BY s.table_name", (ttl_start,))
            rows = await cursor.fetchall()
    except pg_errors.UndefinedTable:
        return {"tables": []}  # the worker has not started yet
    return {"tables": [_status_row(r, now) for r in rows]}


@router.get("/runs")
async def sync_runs(request: Request, table: str | None = None, limit: int = Query(20, ge=1, le=100),
                    before: int | None = Query(None, ge=1), _: CurrentUser = Depends(admin_only)):
    state = request.app.state
    try:
        async with state.db.pool.connection() as conn:
            if table is not None:
                cursor = await conn.execute("SELECT 1 FROM serving.sync_state WHERE table_name = %s", (table,))
                if await cursor.fetchone() is None:
                    raise errors.unknown_table()
            cursor = await conn.execute(
                "SELECT * FROM serving.sync_runs WHERE (%s::text IS NULL OR table_name = %s) "
                "AND (%s::bigint IS NULL OR id < %s) ORDER BY id DESC LIMIT %s",
                (table, table, before, before, limit + 1))
            rows = await cursor.fetchall()
    except pg_errors.UndefinedTable:
        if table is not None:
            raise errors.unknown_table() from None
        return {"items": [], "next_before": None}
    more = len(rows) > limit
    items = [_run_row(r) for r in rows[:limit]]
    return {"items": items, "next_before": items[-1]["id"] if more and items else None}


@router.post("/{table}/run", status_code=202)
async def request_sync(table: str, request: Request, actor: CurrentUser = Depends(admin_only)):
    """Record a request; the worker starts the run within about 5 seconds. One request per table at a time."""
    state = request.app.state
    now = state.clock.now()
    ttl_start = now - timedelta(minutes=state.settings.sync_request_ttl_minutes)
    try:
        async with state.db.transaction() as conn:
            cursor = await conn.execute("SELECT 1 FROM serving.sync_state WHERE table_name = %s", (table,))
            if await cursor.fetchone() is None:
                raise errors.unknown_table()
            cursor = await conn.execute(
                "SELECT outcome FROM serving.sync_runs WHERE table_name = %s ORDER BY id DESC LIMIT 1", (table,))
            latest = await cursor.fetchone()
            if latest is not None and latest["outcome"] == "running":
                raise errors.sync_busy()
            # api_rw cannot UPDATE, so "one at a time" is a conditional insert; a rare double click can slip two rows in
            # and the worker merges them
            cursor = await conn.execute(
                "INSERT INTO serving.sync_requests (table_name, requested_by, requested_at) "
                "SELECT %s, %s, %s WHERE NOT EXISTS (SELECT 1 FROM serving.sync_requests "
                "WHERE table_name = %s AND state = 'pending' AND requested_at > %s) RETURNING id",
                (table, actor.id, now, table, ttl_start))
            if await cursor.fetchone() is None:
                raise errors.sync_busy()
            await audit.record(conn, state.clock, "sync_requested", actor_user_id=actor.id, actor_email=actor.email,
                               target=table, ip=client_ip(request), detail={"table": table})
    except pg_errors.UndefinedTable:
        raise errors.unknown_table() from None
    return {"status": "requested"}
