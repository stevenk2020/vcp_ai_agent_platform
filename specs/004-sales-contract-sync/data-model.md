# Data Model: Sales Contract Sync

All objects are in schema `serving`, owned by `sync_writer`, created by the worker from `sync/sql/*.sql`
(plan research R-4). Nothing changes in `app`, `kb` or `public`.

## Roles and rights (delta to feature 001)

| Role | `serving.sales_contract` | `sync_state`, `sync_runs` | `sync_requests` |
| --- | --- | --- | --- |
| `sync_writer` | owner: create, replace, rename, drop, write | owner: write | owner: read, update |
| `agent_ro` | `SELECT` (existing default privileges) | `SELECT` (existing default privileges) | `SELECT` (same) |
| `api_rw` | **nothing** | `SELECT` (new, explicit) | `SELECT`, `INSERT` (new, explicit) |
| `app_owner`, `backup_ro` | unchanged (`backup_ro` reads all through `pg_read_all_data`) | unchanged | unchanged |

`api_rw` also gets `USAGE` on schema `serving` (bootstrap). It has no right on the copy itself.

## serving.sales_contract (the copy)

64 columns: the source's 66 minus `employee_code_bdd` and `employee_name_bdd` (excluded, FR-020). Every column
is nullable like the source. Full list with meaning, source type and sensitivity: `sync/tables/sales_contract.toml`
(the column dictionary, FR-017) and the generated `docs/data/sales_contract-columns.md`.

| Group | Columns | Copy type |
| --- | --- | --- |
| Text (31) | every `nvarchar` column of the source except the two excluded ones (identifiers such as `entity`, `row_id`, `doc_no`, `customer_code`, `code_old`; names; classifications `cat_1`, `cat_2`; flags; remarks; month and quarter labels; addresses) | `text` |
| Dates (3) | `doc_date`, `finished_date`, `confirmed_date` | `date` |
| Date-times (2) | `created_at`, `update_time` | `timestamp(3)` (no time zone) |
| Whole numbers (10) | `finished_year`, `finished_month_no`, `finished_quarter_no`, `finished_leadtime`, `confirmed_year`, `confirmed_month_no`, `confirmed_quarter_no`, `confirmed_leadtime`, `contract_leadtime`, `city_code` | `integer` |
| Quantities (4) | `quantity_contract`, `quantity_delivered`, `quantity_returned`, `quantity_finished` | `numeric(20,4)` |
| Unit prices (2) | `unit_price_before_tax_contract`, `unit_price_after_tax_contract` | `numeric(24,6)` |
| Money totals (12) | `amount_before_tax_contract`, `amount_tax_contract`, `amount_after_tax_contract`, and the `price_before_tax_*`, `tax_amount_*`, `price_after_tax_*` columns for delivered, returned, finished | `numeric(24,4)` |

Constraints: primary key `(entity, row_id)` (so both are effectively required; a source row with a null in either
refuses the run as `duplicate_key`/`key_missing`). Indexes (created on the staging table, renamed on swap):
`(entity)`, `(confirmed_date)`, `(finished_date)`, `(customer_code)`, `(code_old)`, `(cat_1, cat_2)`.

Replaced as a whole on every successful run; never updated in place.

## serving.sync_state (one row per synced table)

| Field | Type | Notes |
| --- | --- | --- |
| `table_name` | text, primary key | `sales_contract` |
| `definition_hash` | text | hash of the definition file that built the current copy |
| `data_as_of` | timestamptz null | start of the consistent read of the last success (platform clock); null until the first success |
| `last_run_id` | bigint null | latest run, any outcome |
| `last_outcome` | text null | `succeeded`, `failed`, `refused` |
| `last_reason` | text null | reason code of the latest non-success |
| `source_rows`, `copied_rows` | integer null | of the last success |
| `consecutive_failures` | integer | `failed` and `refused` runs since the last success |
| `stale_after_seconds`, `failing_after` | integer | thresholds copied from configuration at each run, so any reader derives the same state |
| `updated_at` | timestamptz | |

Updated in the same transaction as the swap on success (so `data_as_of` always matches the visible data).

State derived by readers: `no_data` (`data_as_of` null), else `failing` (`consecutive_failures >= failing_after`),
else `stale` (now minus `data_as_of` over `stale_after_seconds`), else `healthy`.

## serving.sync_runs

| Field | Type | Notes |
| --- | --- | --- |
| `id` | bigint identity, primary key | |
| `table_name` | text | |
| `trigger` | text | `schedule`, `admin`, `startup`, `command` |
| `requested_by` | uuid null | the admin's person id for `admin` triggers (no name, no email) |
| `started_at`, `finished_at` | timestamptz, null for `finished_at` while running | |
| `outcome` | text | `running` then `succeeded`, `failed`, `refused` |
| `reason_code` | text null | `zero_rows`, `below_fraction`, `below_minimum`, `duplicate_key`, `key_missing`, `shape_changed`, `value_out_of_range`, `source_unreachable`, `read_timeout`, `login_failed`, `privileged_account`, `swap_failed`, `source_not_configured`, `interrupted`, `unexpected` |
| `source_rows`, `copied_rows` | integer null | |
| `duration_ms` | integer null | |
| `error_text` | text null | scrubbed, at most 500 characters (research R-13) |
| `warnings` | jsonb | for example `{"extra_source_columns":["x"],"rounded_values":120}` |

Retention: rows older than `SYNC_LOG_RETENTION_DAYS` (30) are removed after each success.

## serving.sync_requests

| Field | Type | Notes |
| --- | --- | --- |
| `id` | bigint identity, primary key | |
| `table_name` | text | |
| `requested_by` | uuid | admin person id |
| `requested_at` | timestamptz | |
| `state` | text | `pending` then `taken`; `cancelled` if the table is unknown at pickup |
| `run_id` | bigint null | set when taken |

At most one `pending` row per table (partial unique index). The API refuses a new request (409) while a pending row
exists or the latest run is `running`.

## State transitions of a run

`running` -> `succeeded` (swap done) | `refused` (a guard stopped it, copy untouched) | `failed` (source or swap error,
copy untouched). A worker restart turns a leftover `running` row into `failed` / `interrupted`.

## Definition file (not a table): `sync/tables/sales_contract.toml`

Source schema and table, destination name, key, ordered column list (`name`, source type, copy type, nullable,
`meaning`, `sensitivity`, whole-number flag), excluded columns with reason, indexes, per-table guard overrides.
Format in [contracts/sync-definition.md](contracts/sync-definition.md).
