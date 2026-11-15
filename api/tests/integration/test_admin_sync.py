"""US3: admins read the freshness and the run history of the synced tables (contracts/http-api.md)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.admin.sync import derive_state

ADMIN = "admin@example.com"
CASES = json.loads(Path("/sync-tests/state_cases.json").read_text())


def put_state(sw, table="sales_contract", **kw):
    row = {"data_as_of": None, "last_run_id": None, "last_outcome": None, "last_reason": None, "source_rows": None,
           "copied_rows": None, "consecutive_failures": 0, "stale_after_seconds": 1800, "failing_after": 3}
    row.update(kw)
    cols = ", ".join(row)
    sw.execute(f"INSERT INTO serving.sync_state (table_name, {cols}) VALUES (%s, {', '.join(['%s'] * len(row))}) "
               f"ON CONFLICT (table_name) DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in row),
               (table, *row.values()))


def put_run(sw, table="sales_contract", outcome="succeeded", started=None, trigger="schedule", **kw):
    sw.execute(
        "INSERT INTO serving.sync_runs (table_name, trigger, started_at, finished_at, outcome, reason_code, source_rows, "
        "copied_rows, duration_ms, error_text, warnings) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (table, trigger, started, None if outcome == "running" else started, outcome, kw.get("reason_code"),
         kw.get("source_rows"), kw.get("copied_rows"), kw.get("duration_ms", 1000), kw.get("error_text"),
         json.dumps(kw.get("warnings", {}))))
    return sw.execute("SELECT max(id) AS i FROM serving.sync_runs").fetchone()["i"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_derive_state_matches_the_shared_cases(case):
    row = dict(case["row"])
    if row["data_as_of"] is not None:
        row["data_as_of"] = datetime.fromisoformat(row["data_as_of"])
    assert derive_state(row, datetime.fromisoformat(case["now"])) == case["expect"]


async def test_status_has_the_documented_shape_for_the_four_states(login, sw, clock):
    now = clock.now()
    put_state(sw, "t_nodata")
    put_state(sw, "t_healthy", data_as_of=now - timedelta(minutes=5), source_rows=100, copied_rows=100, last_outcome="succeeded")
    put_state(sw, "t_stale", data_as_of=now - timedelta(minutes=31), source_rows=7, copied_rows=7, last_outcome="succeeded")
    put_state(sw, "t_failing", data_as_of=now - timedelta(minutes=5), consecutive_failures=3, last_outcome="refused",
              last_reason="zero_rows", source_rows=100, copied_rows=100)
    admin = await login(ADMIN, role="admin")
    response = await admin.get("/v1/admin/sync")
    assert response.status_code == 200
    tables = {t["table"]: t for t in response.json()["tables"]}
    assert {k: v["state"] for k, v in tables.items()} == {"t_nodata": "no_data", "t_healthy": "healthy", "t_stale": "stale", "t_failing": "failing"}
    assert set(tables["t_healthy"]) == {"table", "state", "data_as_of", "age_seconds", "source_rows", "copied_rows",
                                        "consecutive_failures", "last_outcome", "last_reason", "running", "request_pending",
                                        "stale_after_seconds", "failing_after"}
    assert tables["t_nodata"]["data_as_of"] is None and tables["t_nodata"]["age_seconds"] is None
    assert tables["t_healthy"]["age_seconds"] == 300 and tables["t_stale"]["age_seconds"] == 31 * 60
    assert tables["t_failing"]["last_reason"] == "zero_rows" and tables["t_healthy"]["data_as_of"].startswith(now.strftime("%Y-%m-%dT"))
    assert response.headers["cache-control"] == "no-store"


async def test_running_and_request_pending_flags(login, sw, clock):
    now = clock.now()
    put_state(sw, data_as_of=now - timedelta(minutes=5))
    put_run(sw, outcome="running", started=now)
    sw.execute("INSERT INTO serving.sync_requests (table_name, requested_by, requested_at) VALUES ('sales_contract', gen_random_uuid(), %s)", (now - timedelta(minutes=1),))
    admin = await login(ADMIN, role="admin")
    t = (await admin.get("/v1/admin/sync")).json()["tables"][0]
    assert t["running"] is True and t["request_pending"] is True
    sw.execute("UPDATE serving.sync_runs SET outcome='succeeded', finished_at=now()")
    sw.execute("UPDATE serving.sync_requests SET requested_at = %s", (now - timedelta(minutes=16),))   # older than the 15 minute TTL
    t = (await admin.get("/v1/admin/sync")).json()["tables"][0]
    assert t["running"] is False and t["request_pending"] is False


async def test_runs_are_newest_first_and_page_without_overlap(login, sw, clock):
    now = clock.now()
    put_state(sw)
    ids = [put_run(sw, started=now - timedelta(minutes=10 * (10 - i)), source_rows=i, copied_rows=i) for i in range(10)]
    admin = await login(ADMIN, role="admin")
    first = (await admin.get("/v1/admin/sync/runs?table=sales_contract&limit=4")).json()
    assert [r["id"] for r in first["items"]] == ids[::-1][:4] and first["next_before"] == ids[::-1][3]
    second = (await admin.get(f"/v1/admin/sync/runs?table=sales_contract&limit=4&before={first['next_before']}")).json()
    assert [r["id"] for r in second["items"]] == ids[::-1][4:8]
    last = (await admin.get(f"/v1/admin/sync/runs?table=sales_contract&limit=4&before={second['next_before']}")).json()
    assert [r["id"] for r in last["items"]] == ids[::-1][8:] and last["next_before"] is None
    item = first["items"][0]
    assert set(item) == {"id", "table", "trigger", "started_at", "finished_at", "duration_ms", "outcome", "reason_code",
                         "source_rows", "copied_rows", "error_text", "warnings"}


async def test_run_fields_are_returned_as_stored(login, sw, clock):
    put_state(sw)
    put_run(sw, outcome="refused", started=clock.now(), reason_code="below_fraction", source_rows=9000,
            error_text="source returned 9000 rows, fewer than 50% of the current copy (35440)", warnings={"extra_source_columns": ["x"]})
    admin = await login(ADMIN, role="admin")
    item = (await admin.get("/v1/admin/sync/runs")).json()["items"][0]
    assert (item["outcome"], item["reason_code"], item["source_rows"], item["copied_rows"]) == ("refused", "below_fraction", 9000, None)
    assert item["error_text"].startswith("source returned 9000 rows") and item["warnings"] == {"extra_source_columns": ["x"]}


@pytest.mark.parametrize("query,status", [("limit=0", 422), ("limit=101", 422), ("limit=abc", 422), ("before=zzz", 422)])
async def test_bad_query_values_are_422(login, sw, query, status):
    admin = await login(ADMIN, role="admin")
    assert (await admin.get(f"/v1/admin/sync/runs?{query}")).status_code == status


async def test_unknown_table_is_404(login, sw):
    put_state(sw)
    admin = await login(ADMIN, role="admin")
    response = await admin.get("/v1/admin/sync/runs?table=nope")
    assert response.status_code == 404 and response.json()["error"] == "unknown_table"


async def test_without_the_control_tables_the_status_is_empty_not_an_error(login):
    admin = await login(ADMIN, role="admin")
    response = await admin.get("/v1/admin/sync")
    assert response.status_code == 200 and response.json() == {"tables": []}


@pytest.mark.parametrize("path", ["/v1/admin/sync", "/v1/admin/sync/runs"])
async def test_only_admins_can_read(login, login_pending, new_client, sw, path):
    put_state(sw)
    user = await login("user@example.com", role="user")
    assert (await user.get(path)).status_code == 403
    waiting = await login_pending("wait@example.org")
    assert (await waiting.get(path)).status_code == 401
    assert (await new_client().get(path)).status_code == 401


async def test_responses_hold_no_row_data_or_secret(login, sw, clock):
    put_state(sw, data_as_of=clock.now(), source_rows=1, copied_rows=1, last_outcome="succeeded")
    put_run(sw, started=clock.now(), source_rows=1, copied_rows=1)
    admin = await login(ADMIN, role="admin")
    text = (await admin.get("/v1/admin/sync")).text + (await admin.get("/v1/admin/sync/runs")).text
    for forbidden in ("employee_", "password", "requested_by"):
        assert forbidden not in text.lower()


# ======================= US4: "sync now" (POST) =======================

def pending_rows(sw):
    return sw.execute("SELECT * FROM serving.sync_requests ORDER BY id").fetchall()


async def test_an_admin_request_is_accepted_recorded_and_audited(login, sw, sql, clock):
    put_state(sw, data_as_of=clock.now())
    admin = await login(ADMIN, role="admin")
    admin_id = sql.execute("SELECT id FROM app.users WHERE email = %s", (ADMIN,)).fetchone()["id"]
    response = await admin.post("/v1/admin/sync/sales_contract/run", headers={"X-Forwarded-For": "203.0.113.9"})
    assert response.status_code == 202 and response.json() == {"status": "requested"}
    rows = pending_rows(sw)
    assert len(rows) == 1 and rows[0]["table_name"] == "sales_contract" and rows[0]["state"] == "pending"
    assert str(rows[0]["requested_by"]) == str(admin_id)
    events = sql.execute("SELECT * FROM app.audit_events WHERE action = 'sync_requested'").fetchall()
    assert len(events) == 1
    event = events[0]
    assert event["actor_email"] == ADMIN and event["target"] == "sales_contract" and str(event["ip"]) == "203.0.113.9"
    assert event["detail"] == {"table": "sales_contract"} and event["outcome"] == "success"


async def test_a_second_request_while_one_is_pending_is_busy_and_not_audited(login, sw, sql, clock):
    put_state(sw, data_as_of=clock.now())
    admin = await login(ADMIN, role="admin")
    assert (await admin.post("/v1/admin/sync/sales_contract/run")).status_code == 202
    second = await admin.post("/v1/admin/sync/sales_contract/run")
    assert second.status_code == 409 and second.json()["error"] == "sync_busy"
    assert len(pending_rows(sw)) == 1
    assert sql.execute("SELECT count(*) AS n FROM app.audit_events WHERE action = 'sync_requested'").fetchone()["n"] == 1


async def test_a_request_while_a_run_is_active_is_busy(login, sw, sql, clock):
    put_state(sw, data_as_of=clock.now())
    put_run(sw, outcome="running", started=clock.now())
    admin = await login(ADMIN, role="admin")
    response = await admin.post("/v1/admin/sync/sales_contract/run")
    assert response.status_code == 409 and response.json()["error"] == "sync_busy"
    assert pending_rows(sw) == []
    assert sql.execute("SELECT count(*) AS n FROM app.audit_events WHERE action = 'sync_requested'").fetchone()["n"] == 0


async def test_an_old_pending_request_no_longer_blocks_when_the_worker_is_down(login, sw, sql, clock):
    put_state(sw, data_as_of=clock.now())
    admin = await login(ADMIN, role="admin")
    assert (await admin.post("/v1/admin/sync/sales_contract/run")).status_code == 202
    clock.advance(minutes=14)
    assert (await admin.post("/v1/admin/sync/sales_contract/run")).status_code == 409      # still inside the 15 minutes
    clock.advance(minutes=2)                                                              # 16 minutes in: expired
    assert (await admin.post("/v1/admin/sync/sales_contract/run")).status_code == 202
    assert len(pending_rows(sw)) == 2


async def test_an_unknown_table_is_404_and_not_audited(login, sw, sql):
    put_state(sw)
    admin = await login(ADMIN, role="admin")
    response = await admin.post("/v1/admin/sync/nope/run")
    assert response.status_code == 404 and response.json()["error"] == "unknown_table"
    assert pending_rows(sw) == [] and sql.execute("SELECT count(*) AS n FROM app.audit_events WHERE action = 'sync_requested'").fetchone()["n"] == 0


async def test_non_admins_waiting_people_and_visitors_are_refused(login, login_pending, new_client, sw, sql, clock):
    put_state(sw, data_as_of=clock.now())
    user = await login("user@example.com", role="user")
    assert (await user.post("/v1/admin/sync/sales_contract/run")).status_code == 403
    waiting = await login_pending("wait@example.org")
    assert (await waiting.post("/v1/admin/sync/sales_contract/run")).status_code == 401
    assert (await new_client().post("/v1/admin/sync/sales_contract/run")).status_code == 401
    assert pending_rows(sw) == []


@pytest.mark.parametrize("origin", [None, "https://evil.example"])
async def test_the_origin_guard_applies(login, new_client, sw, clock, origin):
    put_state(sw, data_as_of=clock.now())
    await login(ADMIN, role="admin")
    admin = await login("second-admin@example.com", role="admin")
    other = new_client(origin=origin)
    other.cookies.update(admin.cookies)
    response = await other.post("/v1/admin/sync/sales_contract/run")
    assert response.status_code == 403 and pending_rows(sw) == []


async def test_the_request_text_in_the_audit_log_is_only_the_table(login, sw, sql, clock):
    put_state(sw, data_as_of=clock.now())
    admin = await login(ADMIN, role="admin")
    await admin.post("/v1/admin/sync/sales_contract/run")
    shown = (await admin.get("/v1/admin/audit?action=sync_requested")).json()["items"]
    assert len(shown) == 1 and shown[0]["detail"] == {"table": "sales_contract"}


async def test_run_text_has_no_secret_the_api_returns_what_the_worker_stored(login, sw, clock):
    """The worker scrubs before it stores (sync/tests/test_secrets_in_output.py); the API adds nothing to it."""
    put_state(sw)
    put_run(sw, outcome="failed", started=clock.now(), reason_code="source_unreachable",
            error_text="The source could not be reached: connection to *** failed (user ***, password ***)")
    admin = await login(ADMIN, role="admin")
    body = (await admin.get("/v1/admin/sync/runs")).text
    assert "***" in body and "requested_by" not in body
