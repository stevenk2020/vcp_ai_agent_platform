# The sync worker (`sales_contract`)

The `sync` service copies the SQL Server table `dbo.sales_contract` into the platform database as
`serving.sales_contract`, about every 10 minutes. Everything that answers questions later reads the copy, never the
source. Design: `specs/004-sales-contract-sync/`. Column list: [`docs/data/sales_contract-columns.md`](data/sales_contract-columns.md)
(generated from `sync/tables/sales_contract.toml`).

## What it does, in plain words

1. Takes a lock so only one sync runs at a time.
2. Checks that the source account **cannot write** (not `sysadmin`, not `db_owner`, no INSERT/UPDATE/DELETE on the
   table) and that the source still has the columns it needs. Otherwise it stops and records why.
3. Reads the whole table in one committed read over an **encrypted** connection. While the source job is
   rewriting the table (about 2 minutes, one commit) the read waits for that commit (up to `SOURCE_READ_TIMEOUT_SECONDS`,
   default 300), so it never sees an empty or half-loaded table.
4. Loads the rows into a staging table, checks them (guards below), then swaps staging for the live table in one
   short transaction that also updates "data as of". Readers see the old complete table or the new one, never a mix.
5. If anything is wrong the old copy stays and the run is recorded with a reason code.

Two columns are **never read**: `employee_code_bdd` and `employee_name_bdd` (staff personal data; decision 2026-11-15). Questions
about salespeople cannot be answered from the copy.

## Check the state

| Where | How |
| --- | --- |
| Admin page | `/admin#data`: state badge, "data as of", counts, the last 20 runs, a **Sync now** button |
| Terminal | `make sync-status` (state, data as of, counts, the last 5 runs per table) |
| Database | `SELECT * FROM serving.sync_state;` (readable by the read-only agent role too) |

States: **Healthy** (a recent success), **Stale** (the last success is older than `SYNC_STALE_MINUTES`, default 30),
**Failing** (`SYNC_FAILING_AFTER` failed or refused runs in a row, default 3), **No data yet** (never succeeded).
A failed or refused run never changes "data as of"; the previous copy keeps being served.

## Force a sync

- Admin page: **Sync now** (asks for confirmation; the worker starts within about 5 seconds; audited as `sync_requested`).
- Terminal: `make sync-now` (same lock as the scheduled runs; exit code 0 succeeded, 2 refused, 3 failed, 4 another run is active).
- If the source is legitimately empty (its owner wiped the table on purpose) the guard refuses to replace the copy.
  Confirm it with the owner, then run `make sync-now ARGS="--allow-empty"` once.

## Set up or change the source credentials

```bash
make source-secret        # copies sql_server_* from .env into secrets/source_sqlserver (prints key names only)
make up-prod              # rebuilds and restarts; the worker starts reading
make sync-status
```

You can delete the `sql_server_*` lines from `.env` afterwards. With an empty `secrets/source_sqlserver` the worker stays
healthy and idle and says `source_not_configured` in its log; an admin request or `make sync-now` then records one failed
run with that reason.

## Encryption

`SOURCE_ENCRYPT=require` (the default) makes the connection fail if the server cannot encrypt, so the password and the data
never cross the network unprotected. The source accepts it (checked 2026-11-15). Any other value (`request`, `off`, `default`)
logs a warning at every start. The account cannot read connection details on the server, so the guarantee is the `require`
setting itself, not a query.

## Runbook

**The source is down or unreachable** (`source_unreachable`, `login_failed`, `read_timeout`). The worker retries connection
errors twice (waits 15 s and 45 s), then records the failure and tries again at the next 10 minute tick. The copy keeps
serving and turns **Stale** after 30 minutes and **Failing** after 3 failed runs. When the source is back the next run
succeeds on its own; nothing to restart. If it says `login_failed`, the password changed: update `.env`, run
`make source-secret ARGS=--force`, then `docker compose restart sync`. If it says `read_timeout` repeatedly, the source
job may be running longer than 5 minutes: ask its owner, or raise `SOURCE_READ_TIMEOUT_SECONDS`.

**A guard refused the run.** The copy is untouched. The run text says which:

| Reason | Meaning | What to check |
| --- | --- | --- |
| `zero_rows` | the source returned no rows | is the source job mid-rebuild or broken? Ask the owner. Only if the table is really empty on purpose: `--allow-empty` |
| `below_fraction` | fewer than 50% of the current rows | a partial load at the source? Wait one cycle; if it persists, ask the owner. A real large clean-up: lower `GUARD_MIN_FRACTION` for one run |
| `below_minimum` | fewer than `GUARD_MIN_ROWS` (1000) rows | same as above; on the very first sync check the source table is the right one |
| `duplicate_key` / `key_missing` | two rows with the same `(entity, row_id)`, or a null key | a source data problem; ask the owner. The run text never shows row values |
| `value_out_of_range` | a value does not fit its column (for example a fractional "whole number" column) | the text names the column; tell the agent maintainers to widen it |
| `shape_changed` | a needed column is missing or changed type | see the next paragraph |
| `privileged_account` | the source account can write or is an administrator | ask the DBA for a SELECT-only account; the worker refuses to run until then |

**The source shape changed.** A missing or retyped column refuses the run (the text lists names only). To follow the change:
edit `sync/tables/sales_contract.toml` (column, type, meaning), bump `version`, run
`cd sync && python -m sync.docgen ../docs/data ./tables` to regenerate the dictionary, run `make test-sync`, redeploy.
A column that was **added** in the source is ignored and shown as a warning on the run; it is not copied until you add it.

**The first run is slow.** Up to about 2 minutes while the source job commits is normal.

## Turn on snapshot reads (when the DBA enables them)

While `ALLOW_SNAPSHOT_ISOLATION` is OFF on the source, each read waits for the source's own commit. When the DBA turns it ON
(their action: `ALTER DATABASE ... SET ALLOW_SNAPSHOT_ISOLATION ON`), set `SOURCE_SNAPSHOT_READ=on` in `.env`, run
`make up-prod`, and check the log says `"snapshot_read": "on"`. Runs then take seconds and never wait. If you set it to `on`
while the source still has it off, the service refuses to start and says so. Then record V-4 as resolved in
`docs/phase0-verification.md`.

## For later features

`serving.sync_state` holds `data_as_of`, `copied_rows`, `consecutive_failures`, `stale_after_seconds` and `failing_after` for each
table, updated in the same transaction as the swap. Derive the state with the same rules everywhere:
no `data_as_of` is *no data*; `consecutive_failures >= failing_after` is *failing*; age over `stale_after_seconds` is *stale*;
else *healthy*. The run history (`serving.sync_runs`) and the admin requests (`serving.sync_requests`) are not readable by the
agent role.

## Backups

The copy and the control tables are part of the normal Postgres dump (`make backup`). After a restore, the next sync (at most
10 minutes later) repairs the copy.

A backup taken while a swap happens is consistent: `pg_dump` reads one snapshot, so it contains the old complete table or the
new one.

## Settings

All settings are environment variables of the `sync` service (defaults in `compose.yaml`; full table in
`specs/004-sales-contract-sync/contracts/sync-definition.md`): `SYNC_INTERVAL_MINUTES`, `SOURCE_SNAPSHOT_READ`,
`SOURCE_READ_TIMEOUT_SECONDS` (at least 180), `SOURCE_ENCRYPT`, `SYNC_RETRY_ATTEMPTS`, `GUARD_MIN_FRACTION`, `GUARD_MIN_ROWS`,
`SYNC_STALE_MINUTES`, `SYNC_FAILING_AFTER`, `SYNC_REQUEST_TTL_MINUTES`.
