# Contract: admin sync endpoints (additive to feature 002/003)

All paths need an approved **admin** session and the Origin check on `POST`, exactly like the other `/v1/admin/*`
routes. A non-admin, a waiting person and a signed-out visitor get the same refusals as for the other admin routes
(`403` / `401`). Nothing here reads SQL Server or the copy's rows.

## `GET /v1/admin/sync`

`200`:

```json
{
  "tables": [
    {
      "table": "sales_contract",
      "state": "healthy",
      "data_as_of": "2026-11-15T09:50:12Z",
      "age_seconds": 183,
      "source_rows": 35440,
      "copied_rows": 35440,
      "consecutive_failures": 0,
      "last_outcome": "succeeded",
      "last_reason": null,
      "running": false,
      "request_pending": false,
      "stale_after_seconds": 1800,
      "failing_after": 3
    }
  ]
}
```

`state` is one of `no_data`, `healthy`, `stale`, `failing`. `data_as_of` and `age_seconds` are `null` for `no_data`.

## `GET /v1/admin/sync/runs?table=sales_contract&limit=20&before=<run id>`

`limit` 1 to 100 (default 20). Newest first. `200`:

```json
{
  "items": [
    {
      "id": 4812, "table": "sales_contract", "trigger": "schedule",
      "started_at": "...", "finished_at": "...", "duration_ms": 118442,
      "outcome": "refused", "reason_code": "below_fraction",
      "source_rows": 9000, "copied_rows": null,
      "error_text": "source returned 9000 rows, fewer than 50% of the current copy (35440)",
      "warnings": {}
    }
  ],
  "next_before": 4793
}
```

Unknown table: `404 {"error":"unknown_table"}`. Bad `limit`: `422`.

## `POST /v1/admin/sync/{table}/run`

No body. Records a request; the worker starts it within about 5 seconds.

| Result | When |
| --- | --- |
| `202 {"status":"requested"}` | accepted; audited as `sync_requested` with `{"table": ...}` (no other detail) |
| `409 {"error":"sync_busy"}` | a request is pending or a run is active |
| `404 {"error":"unknown_table"}` | table not registered by the worker |
| `403` / `401` | not an admin / not signed in |

## Audit

New action `sync_requested` in the existing audit log (actor = the admin, target = the table name). The runs
themselves are recorded in `sync_runs` (not in the audit log).

## For later features (Topic service)

Not an HTTP contract: `serving.sync_state` is readable by `agent_ro`; the Topic service shows `data_as_of` from it.
Derive the state with the rules in data-model.md.
