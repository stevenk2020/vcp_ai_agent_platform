# Contract: copy definition, configuration and operator commands

## Definition file `sync/tables/<name>.toml`

```toml
[table]
source = "dbo.sales_contract"        # schema.table in SQL Server
copy = "sales_contract"              # table in schema serving
key = ["entity", "row_id"]
version = 1                          # bump when anything below changes; also hashed

[guards]                             # optional per-table overrides of the global settings
min_fraction = 0.5
min_rows = 1000

[[columns]]                          # order = order in the copy and in the source SELECT
name = "entity"
source_type = "nvarchar"             # must match INFORMATION_SCHEMA, else shape_changed
copy_type = "text"                   # text | date | timestamp(3) | integer | numeric(P,S)
meaning = "Company that owns the contract line: vcp, salud, nufa or durabe"
sensitivity = "none"                 # none | business_address | (excluded columns are listed below, not here)

[[excluded]]                         # never selected, never stored, never logged
name = "employee_name_bdd"
reason = "staff personal data (constitution: sensitive columns masked or excluded); decision 2026-11-15"

[[indexes]]
name = "sales_contract_entity_idx"
columns = ["entity"]
```

Rules enforced by a unit test: every column has `meaning`; no excluded name appears in `[[columns]]`; every key column
exists; whole-number columns are `integer`; the generated `docs/data/sales_contract-columns.md` equals the file's
content (the dictionary cannot drift).

## Configuration (environment of the `sync` service; defaults)

| Variable | Default | Meaning |
| --- | --- | --- |
| `SYNC_INTERVAL_MINUTES` | 10 | schedule per table |
| `SYNC_POLL_SECONDS` | 5 | how often pending admin requests are picked up |
| `SOURCE_SNAPSHOT_READ` | `off` | `on` once the DBA enabled snapshot isolation (worker refuses to start if `on` and the source says it is off) |
| `SOURCE_READ_TIMEOUT_SECONDS` | 300 | lock wait and query limit; values below 180 are refused (the source job takes about 2 minutes) |
| `SOURCE_ENCRYPT` | `require` | `require`, `request`, `off`, `default`; anything but `require` logs a warning at every start |
| `SYNC_REQUEST_TTL_MINUTES` | 15 | a pending admin request older than this no longer blocks new ones and is cancelled by the worker (also set on the `api` service) |
| `SYNC_RETRY_ATTEMPTS`, `SYNC_RETRY_BASE_SECONDS` | 3, 15 | connection retries; waits 15 s, 45 s |
| `GUARD_MIN_FRACTION`, `GUARD_MIN_ROWS` | 0.5, 1000 | guards (table file overrides) |
| `SWAP_LOCK_TIMEOUT_SECONDS` | 15 | lock wait for the swap (3 tries) |
| `SYNC_STALE_MINUTES`, `SYNC_FAILING_AFTER` | 30, 3 | stored beside the data |
| `SYNC_LOG_RETENTION_DAYS` | 30 | run history kept |

Secret file `secrets/source_sqlserver` (mounted as `/run/secrets/source_sqlserver`), `KEY=value` lines:
`SQL_SERVER_HOST`, `SQL_SERVER_PORT`, `SQL_SERVER_USER`, `SQL_SERVER_PASSWORD`, `SQL_SERVER_DATABASE`. Empty file:
the worker idles with "source not configured". The Postgres password for `sync_writer` comes from the existing
`sync_writer_password` secret.

## Operator commands (additions to the 001 operator-commands contract)

| Command | Effect |
| --- | --- |
| `make source-secret` | copies the five `sql_server_*` values from `.env` into `secrets/source_sqlserver` (mode 600), prints no value |
| `make up` / `make up-prod` | start the `sync` service with the rest (no change in how they are called) |
| `make sync-status` | prints the state, data as of, counts and the last 5 runs from the database |
| `make sync-now` | `docker compose exec sync python -m sync run sales_contract`: runs one sync now under the same lock (add `ARGS="--allow-empty"` for a legitimately empty source) |
| `make test-sync` | isolated test project `platform-synctest`, fake source, throwaway Postgres |

## Service behaviour contract

1. Start: load config and secrets; apply `sync/sql` (idempotent); register every table found in `sync/tables/*.toml` in `sync_state`; fail old
   `running` rows (`interrupted`); verify the source account (R-11) and, if `SOURCE_SNAPSHOT_READ=on`, the
   source's snapshot state; with no source secret, the scheduled runs are skipped and only the heartbeat and the request poll keep running (a request or a command then records one `source_not_configured` run).
2. Each run: take the advisory lock or stop; insert the `running` row; check account; check shape; read the whole
   table with the explicit column list into the staging table; guards; swap; update state; release.
3. Heartbeat: the scheduler touches a file at start and then every 30 s; the container health check fails when it is older than 90 s.
4. Logs: one JSON line per run event; never a secret, never row values.
