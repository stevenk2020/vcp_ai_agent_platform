# Research: Sales Contract Sync

Facts below were measured on the live source on 2026-11-15 with the read-only account (SELECT only; no value of
personal data is recorded here). Source: SQL Server 2019 (15.0.4335), database snapshot isolation OFF.

## R-1: How the worker talks to SQL Server

- **Decision**: Python package `pymssql` (bundled FreeTDS, no system driver). Used synchronously in the worker.
- **Rationale**: it connected and read 35,440 x 66 from this VPS with no OS packages; the Microsoft ODBC driver would
  add an apt repository, a licence prompt and a larger image for no gain at this size.
- **Alternatives**: `pyodbc` + msodbcsql18 (heavier image, EULA); `python-tds` (pure Python, slower, less used);
  `mssql-python` (new, less proven).
- **Gotcha recorded**: a plain (non-Unicode) literal in a `WHERE` on Vietnamese text silently matches nothing. The worker
  never filters on text: it reads the whole table with an explicit column list and bound parameters only.

## R-2: Where the worker runs and how it is scheduled

- **Decision**: a new Compose service `sync` (own small image under `sync/`, Python 3.12, `apscheduler`, `psycopg`,
  `pymssql`). APScheduler `BlockingScheduler` with two jobs per table: a refresh job every `SYNC_INTERVAL_MINUTES`
  (default 10) and a request-poll job every 5 seconds. `max_instances=1`, `coalesce=True`.
- **Rationale**: the constitution names an APScheduler sync worker; a separate service keeps SQL Server drivers and
  credentials out of the `api` container (Principle II: the API never touches SQL Server).
- **Alternatives**: run inside `api` (rejected: puts the source credentials and a 2-minute blocking read next to the
  web process); cron in a container (no on-demand path, no in-process lock).

## R-3: Never two runs at once

- **Decision**: a Postgres session advisory lock (`pg_try_advisory_lock`, key derived from the table name) on a
  dedicated connection held for the whole run. A run that cannot take it stops at once ("already running").
- **Rationale**: it holds across several worker processes, is released by Postgres itself if the worker dies (no
  stuck lock after a restart), and needs no extra table or Redis key.
- **Alternatives**: a `running` flag in a table (stale after a crash, needs cleanup logic); Redis lock (extra
  moving part, TTL guessing).
- **Crash handling**: at start the worker marks any `running` run row that has no live lock as `failed`
  (`interrupted`).

## R-4: Where the copy and the sync records live (and why not a migration)

- **Decision**: everything in schema `serving`, owned by `sync_writer`: the copy `serving.sales_contract`, plus
  control tables `sync_state`, `sync_runs`, `sync_requests`. They are created by the worker at start from versioned
  SQL files in `sync/sql/` (idempotent; the applied version is stored in `sync_state`). `api_rw` receives `USAGE` on
  schema `serving` (bootstrap) and explicit grants on the three control tables only (given by the same SQL file);
  `agent_ro` reads `sync_state` (needed for `data_as_of`) through the existing default privileges; `control.sql` revokes it from `sync_runs` and `sync_requests` (admin person ids and error text are not for the agent).
- **Rationale**: (1) `sync_writer` is the only role allowed to create objects in `serving` (001 contract); migrations
  run as `app_owner`, which must stay unable to touch `serving`. (2) The freshness record has to change in the
  *same transaction* as the swap so a reader never sees new data with an old "data as of" or the reverse; that is only
  possible if it is in a schema `sync_writer` writes. (3) Since every refresh is a full replace, a changed table
  definition is simply built into the next staging table: there is no data migration to protect.
- **Consequence**: spec FR-016 is reworded ("versioned idempotent definitions applied by the sync identity"), and the
  001 contract `database-access.md` gets a dated amendment: `api_rw` may read `sync_state` and `sync_runs` and insert
  into `sync_requests` in `serving`, nothing else there (proven by foundation tests).
- **Alternatives**: control tables in `app` via migration (needs `sync_writer` rights on `app`, widening a role that
  001 deliberately keeps out; and the freshness could not be in the swap transaction); a second DB role for the worker
  (a new secret and role for no extra safety).

## R-5: Atomic replacement

- **Decision**: build `serving.sales_contract__new` (same columns, primary key, no other index yet), stream the rows
  into it with `COPY`, create the secondary indexes on it, `ANALYZE` it, run the guards, then one short transaction:
  `lock_timeout 15s`; `ALTER TABLE ... RENAME TO sales_contract__old`; `DROP TABLE sales_contract__old` (frees the
  index names); `ALTER TABLE __new RENAME TO sales_contract`; rename the new indexes to their final names; update
  `sync_state` and the run row; commit. If the lock cannot be taken in 15 s (a long reader), retry up to 3 times,
  then the run fails and the old copy stays.
- **Rationale**: same pattern as the existing foundation test `tests/foundation/test_swap.py` (and the bootstrap's
  default privileges already make the swapped-in table readable by `agent_ro`). Readers see the old complete table
  until commit and the new complete table after; the lock is held for milliseconds. Principle II requires staging +
  atomic swap.
- **Alternatives**: `TRUNCATE` + `INSERT` in one transaction (readers blocked for the whole insert, about seconds);
  `DELETE`+`INSERT` (bloat, long locks); a view that points at one of two tables (adds a moving part).

## R-6: One consistent read of the source

- **Decision**: setting `SOURCE_SNAPSHOT_READ` = `off` (default) or `on`.
  - `off`: `READ COMMITTED` with `SET LOCK_TIMEOUT` equal to the read limit (default 300 s) and `SET DEADLOCK_PRIORITY LOW`
    so the worker, never the source job, is the deadlock victim. The `SELECT` waits for the source's commit (V-3: one
    commit, about 2 minutes) and then reads the new complete state.
  - `on`: `SET TRANSACTION ISOLATION LEVEL SNAPSHOT`; at start the worker checks `sys.databases.snapshot_isolation_state`
    is `1` and refuses to start otherwise (V-4).
  - Never `NOLOCK`/`READUNCOMMITTED` (constitution).
- **Rationale**: matches the answers (about every 10 minutes, about 2 minutes, one commit): a committed read can never
  see the table empty or half-inserted. Whole-table `SELECT` as one statement is one consistent read under both modes.
- **Alternatives**: `READ_COMMITTED_SNAPSHOT` (changes source behaviour for everyone: not ours to set); reading in
  chunks (could mix two source states).
- **Scheduling consequence**: a run that is waiting does not overlap the next tick (R-3), so a 2-minute wait only
  delays that run.

## R-7: Guards

- **Decision**: checked on the staging table before the swap, each with a stable reason code:
  `zero_rows`; `below_fraction` (source rows < `GUARD_MIN_FRACTION` x current copy rows, default 0.5); `below_minimum`
  (source rows < `GUARD_MIN_ROWS`, default 1000, also applies to the first sync); `duplicate_key` (the staging primary
  key rejects a repeated `(entity, row_id)`); `shape_changed` (a column the copy needs is missing or has another type,
  checked from `INFORMATION_SCHEMA.COLUMNS` before reading); `value_out_of_range` (a value does not fit its column).
  Extra source columns only add a warning to the run record.
- **Rationale**: the highest risk is a silently emptied or truncated copy. Thresholds are configuration, per table
  overridable in the definition file.
- **Legitimate empty source**: documented operator action (set the minimum to 0 for one run via the admin command
  `--allow-empty`, not an automatic path).

## R-8: Types (decided and documented, spec FR-011)

Measured on all 35,440 rows:

| Source | Findings | Copy type |
| --- | --- | --- |
| `nvarchar(n)` (33 columns, 31 copied) | Vietnamese text, empty strings and nulls both occur | `text` (no length change; empty stays empty) |
| `date` (3) | | `date` |
| `datetime` (2: `created_at`, `update_time`) | no time zone, 3.33 ms precision | `timestamp(3)` (no zone assumed) |
| `float` year/month/quarter/leadtime/`city_code` (10) | all whole numbers (month number is `YYYYMM`, quarter is `YYYYQQ`, up to 202808; lead time can be negative: data quality) | `integer`; the run is refused if a non-whole value appears |
| `float` quantities (4) | whole today | `numeric(20,4)` |
| `float` money totals (12) | whole dong today, up to 2.2e12 | `numeric(24,4)` |
| `float` unit prices (2) | up to 14 decimals of floating noise (e.g. 7839.9993000000001) | `numeric(24,6)`, rounded half-even to 6 decimals |

Conversion rule: float to decimal goes through the shortest round-trip text of the float (so 7839.9993 becomes
7839.9993, never the binary expansion), then is rounded to the column scale; a value that does not fit the precision
refuses the run. The run record counts rounded values as a warning. Totals are sums of exact decimals, so later sums
no longer drift.

## R-9: Sensitive columns (spec FR-020, decision: option A)

- **Decision**: the copy definition lists the columns explicitly (64 of 66). `employee_code_bdd` and `employee_name_bdd`
  are excluded and are never part of the source `SELECT`. `customer_address` and `address` are kept, flagged
  `sensitivity: business_address`.
- **Rationale**: staff personal data is not needed for the first topic's questions and the name is missing in 54% of
  rows; customer addresses are company addresses. A test fails if the excluded names ever appear in the `SELECT` text
  or the copy.

## R-10: Source credentials

- **Decision**: a Compose secret `source_sqlserver` (file `secrets/source_sqlserver`, `KEY=value` lines, the same
  pattern as `api_external`), mounted only into the `sync` service. `make secrets` creates an empty template;
  `scripts/source-secret-from-env.sh` copies the five `sql_server_*` values from the operator's `.env` without
  printing them. With an empty file the worker idles and reports "source not configured", so `make up` and CI keep
  working. After copying, the operator can delete the credentials from `.env`.
- **Rationale**: keeps the repository rule "no `env_file`, secrets only as files in `/run/secrets`".

## R-11: Read-only proof of the source account

- **Decision**: before every run the worker executes one query that returns `IS_SRVROLEMEMBER('sysadmin')`,
  `IS_MEMBER('db_owner')` and `HAS_PERMS_BY_NAME(<table>, 'OBJECT', 'INSERT'|'UPDATE'|'DELETE')`. Any positive value
  refuses the run (`privileged_account`). The only statements the worker sends are that check, the `INFORMATION_SCHEMA`
  read, the `sys.databases` read, the `SET` options and the table `SELECT`; a unit test records every statement the
  real connection wrapper sends and asserts that.
- **Measured today**: sysadmin 0, db_owner 0, insert 0, update 0, delete 0, select 1.

## R-12: Retries and the failing state

- **Decision**: connection-level errors (cannot connect, connection lost, login timeout) retry up to
  `SYNC_RETRY_ATTEMPTS` (3) with waits of 15 s, 45 s (x3 each time). A read-limit timeout, a guard refusal and a login
  failure do not retry inside the run; they are recorded and the next tick tries again. `consecutive_failures` counts
  `failed` and `refused` runs; at `SYNC_FAILING_AFTER` (3) the derived state is `failing`.
- **State rules** (derived at read time, thresholds are stored beside the data): `no_data` (no success ever), `failing`,
  `stale` (age over `SYNC_STALE_MINUTES`, default 30), else `healthy`. Precedence: no_data, failing, stale, healthy.

## R-13: Error text without secrets

- **Decision**: `reason_code` (fixed set) plus a short fixed human sentence; the raw driver message is appended only
  after a scrub that replaces the source host, user, password, database name and the Postgres passwords with
  `***`, limited to 500 characters. Logs go through the same scrub. Unit tests feed driver messages that contain each
  secret and assert none survives.

## R-14: Admin on-demand sync

- **Decision**: `POST /v1/admin/sync/{table}/run` inserts a row into `serving.sync_requests` only when no pending request
  for the table is newer than `SYNC_REQUEST_TTL_MINUTES` (default 15) and no run is active (otherwise 409), audited as
  `sync_requested`. Because `api_rw` cannot update rows, the worker cancels expired pending rows and merges duplicates. The worker polls every 5 s, takes the
  request, runs it, and links the request to the run. A server command exists too: `make sync-now`
  (`docker compose exec sync python -m sync run sales_contract`), also subject to the lock, for break-glass use.
- **Rationale**: the API never opens a connection to SQL Server or to the worker; a database row is the whole
  interface, and it survives a worker restart.

## R-15: Admin pages

- **Decision**: one more tab, "Data", in the existing admin page: one status line per table (state badge, "data as
  of", age, counts), the last 20 runs with outcome and reason, and a "Sync now" button behind the existing confirm
  dialog. Same rules as feature 003 (text only through `textContent`, no inline script, `expect`-style conflict
  message on 409).

## R-16: Retention of the run log

- **Decision**: `SYNC_LOG_RETENTION_DAYS` (default 30); the worker deletes older `sync_runs` rows after each
  successful run (about 4,300 rows at one run per 10 minutes).

## R-17: Tests and CI

- **Decision**: a new isolated target `make test-sync` (Compose project `platform-synctest`, like `make test-api`)
  runs the sync tests against a throwaway Postgres with a fake source; no real SQL Server in CI. Foundation tests
  gain permission checks for the new grants and the swap with a concurrent reader. API tests gain admin sync
  endpoints, creating the control tables from the same SQL file with the `sync_writer` role.
- **Residual risk (recorded, not hidden)**: the fake source cannot reproduce a real SQL Server lock wait; the
  operator validates one real run with the quickstart (steps 2 and 6).

## R-18: Encrypted connection to the source

- **Decision**: setting `SOURCE_ENCRYPT` = `require` (default) | `request` | `off` | `default`, passed to the driver's
  `encryption` option. The worker logs a warning at every start when it is not `require`.
- **Evidence**: on 2026-11-15 a connection with `encryption='require'` succeeded and ran `SELECT 1` against the real
  source; an invalid value is rejected by the driver (so the option is honoured, not ignored). The account cannot read
  `sys.dm_exec_connections` (no `VIEW SERVER STATE`), so the connection's encrypted state cannot be queried; the
  guarantee is that `require` makes the connection fail if the server cannot encrypt.
- **Rationale**: the login password and about 35k rows of business data cross the network on every run.
- **Alternatives**: trust the network (rejected: unknown path to the source); a VPN or tunnel (possible later, not needed
  now that the server speaks TLS).
