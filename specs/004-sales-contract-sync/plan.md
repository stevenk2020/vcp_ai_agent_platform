# Implementation Plan: Sales Contract Sync

**Branch**: `dev_spec_04` | **Date**: 2026-11-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/004-sales-contract-sync/spec.md`

## Summary

A new Compose service `sync` (small Python image, APScheduler) copies `dbo.sales_contract` from SQL Server into
`serving.sales_contract` every 10 minutes and on an admin request. Each run takes a Postgres advisory lock (never two
at once), checks that the source account cannot write, checks the source's column set and types, reads the whole table
in one committed read (waiting for the source's own commit, or snapshot isolation when the operator switches it on),
streams it with `COPY` into a staging table, applies guards (zero rows, below 50% of the current copy, below a minimum,
duplicate keys, shape change, value out of range), and swaps staging for the live table in one short transaction that
also updates the freshness record. On any problem the previous copy stays and the run is recorded with a reason code and a
scrubbed message. The copy has 64 of the 66 columns (the two salesperson columns are excluded), exact decimals for
money, integers for whole-number floats. The API gains three admin endpoints (status, run history, "sync now"), the
admin page gains a "Data" tab, and everything is proven with a fake source. See [research.md](research.md) for each
decision, [data-model.md](data-model.md) and [contracts/](contracts/).

## Technical Context

**Language/Version**: Python 3.12 (worker and API); one added admin-page script section (plain ES modules, as in 003)

**Primary Dependencies**: new worker image only: `pymssql`, `apscheduler`, `psycopg[binary]` (psycopg already used by the API); `tomllib` from the standard library; API image unchanged apart from code

**Storage**: PostgreSQL schema `serving`: the copy table and three control tables (`sync_state`, `sync_runs`, `sync_requests`), owned by `sync_writer`; nothing new in `app`, nothing in Redis

**Testing**: new isolated `make test-sync` (pytest, Compose project `platform-synctest`, fake source); extended `make test-api` and `make test` (foundation grants, swap with a concurrent reader); the real SQL Server is never needed by automated tests

**Target Platform**: the existing single Ubuntu VPS, Docker Compose; the worker reaches SQL Server over the outbound network (TCP reachable from this VPS, confirmed in V-1 and exercised by a read on 2026-11-15)

**Project Type**: background worker service plus additive API and admin-page changes

**Performance Goals**: copy within 15 minutes of a source change; a full run of 35k rows x 64 columns under 5 minutes including the wait for the source's commit (SC-001, SC-002); swap lock held for milliseconds

**Constraints**: the source is only read (explicit column list, no `NOLOCK`, deadlock priority low); excluded columns never selected; secrets only as files; no secret in logs or error texts; readers never see an empty or partial copy; one run at a time

**Scale/Scope**: one table, about 35,000 rows (measured 35,440), 64 columns, one run about every 10 minutes (about 144 runs a day, about 4,300 run rows kept)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design (below).*

| Principle | Applies here? | Status |
| --- | --- | --- |
| I. Permission-First Security | Yes: admin endpoints and a new DB grant for `api_rw` | Pass. Admin-only, Origin-checked, audited (`sync_requested`); the new `api_rw` rights are the minimum (read two control tables, insert requests) and are proven by tests; deny-by-default route walk keeps covering the new routes |
| II. Data Isolation and Safe SQL | Yes: this is the sync | Pass. Only the worker touches SQL Server; staging table plus atomic swap into `serving`; row-count sanity check (plus other guards); no `NOLOCK`; `agent_ro` stays read-only on `serving` |
| III. Untrusted Content and Contained Execution | Partly: source text is stored as data | Pass. Values are copied as data through parameterised `COPY`; the admin page shows run text with `textContent` only |
| IV. Small Core, Pluggable Everything | Yes | Pass. The worker lives outside the Agent Platform and the API (no SQL Server or topic logic in the API); table list and columns are configuration so a second table is a new definition file |
| V. Verifiable Answers | Prepares it | Pass. `data_as_of` is produced atomically with the data for the later topic feature to display |
| VI. Observability and Auditability | Yes | Pass. Every run is recorded (counts, duration, outcome, reason); manual triggers are audited; logs are JSON without secrets or row values |
| Technology and Operational Constraints | Yes | Pass. APScheduler sync worker as named in the constitution; one new third-party driver (`pymssql`), justified in R-1; sensitive columns excluded (rule: flagged and masked or excluded); secrets only as files |
| Development Workflow | Yes | Pass. Phase 0 verification items V-2 and V-3 are answered (constitution requires them before the worker); V-4 is not a blocker (it is a switch); every task gets an acceptance test |

**Complexity Tracking**:

| Deviation | Why needed | Simpler/standard alternative rejected because |
|-----------|------------|-----------------------------------------------|
| Control tables and the copy are created by the worker from versioned SQL, not by a dbmate migration (spec FR-016 reworded) | Only `sync_writer` may create objects in `serving`; the freshness row must change in the swap transaction | A migration would need `app_owner` to touch `serving` or `sync_writer` to touch `app`, both of which feature 001 forbids on purpose (R-4) |
| Feature 001 contract amended: `api_rw` gets `USAGE` on `serving` and three explicit table grants | The admin API must read freshness and runs and record a "sync now" request without a new DB role | A new role or an HTTP channel to the worker adds a secret or a service for no safety gain |
| New dependency `pymssql` and a new service image | The source is SQL Server; the API image must not hold its credentials | Reading from the API container would break Principle II's isolation of the operational database |

**Post-design re-check**: Pass. data-model.md keeps `api_rw` away from the copy's rows and `agent_ro` read-only;
contracts keep the admin-only rules. Residual risks are recorded in research (R-17: the fake source cannot reproduce
a real SQL Server lock wait; the quickstart validates it once).

## Project Structure

### Documentation (this feature)

```text
specs/004-sales-contract-sync/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md          # admin sync endpoints
│   └── sync-definition.md   # definition file, configuration, operator commands, service behaviour
├── checklists/requirements.md
└── tasks.md                 # created by /speckit-tasks
```

### Source Code (repository root)

```text
sync/                                  # NEW service
├── Dockerfile                         # python:3.12-slim; targets runtime and test
├── pyproject.toml                     # pymssql, apscheduler, psycopg[binary]; dev: pytest
├── sync/
│   ├── __main__.py                    # CLI: run | status | drill ; no args = start the scheduler
│   ├── config.py                      # settings, secrets, validation
│   ├── definition.py                  # parse and check sync/tables/*.toml, hash, SQL generation
│   ├── source.py                      # Source protocol; SqlServerSource (pymssql); statement recorder
│   ├── store.py                       # Postgres: control tables, staging, COPY, swap, state, runs, requests
│   ├── runner.py                      # one run: lock, checks, read, guards, swap, record
│   ├── guards.py                      # zero rows, fraction, minimum, shape, key, range
│   ├── convert.py                     # float to Decimal rule, whole-number check, row to COPY text
│   ├── scrub.py                       # secret scrubbing for logs and error text
│   └── scheduler.py                   # APScheduler jobs, request poll, heartbeat
├── sql/
│   └── control.sql                    # idempotent: control tables, grants to api_rw, version
├── tables/sales_contract.toml         # the definition and column dictionary
└── tests/                             # pytest with FakeSource against a throwaway Postgres
    ├── fakes.py, conftest.py
    └── test_copy.py, test_swap_atomic.py, test_guards.py, test_retries.py, test_lock.py,
        test_scrub.py, test_readonly.py, test_definition.py, test_requests.py, test_state.py
api/app/admin/sync.py                  # NEW: three endpoints (included in main.py)
api/app/audit.py                       # CHANGED: action sync_requested
api/app/web/admin.html, admin-assets/admin.js   # CHANGED: "Data" tab
api/tests/integration/test_admin_sync.py        # NEW (control tables created by the same sql file as sync_writer)
api/tests/unit/test_page_sources.py             # CHANGED: new tab covered by the existing lint rules
db/bootstrap/30-privileges.sql         # CHANGED: GRANT USAGE ON SCHEMA serving TO api_rw
compose.yaml                           # CHANGED: service sync, secret source_sqlserver, sync-test wiring
Makefile, scripts/                     # CHANGED: source-secret, sync-status, sync-now, test-sync; gen-secrets template
tests/foundation/test_permissions.py, test_swap.py, test_secrets_scope.py   # CHANGED/NEW cases
.github/workflows/ci.yml               # CHANGED: make test-sync step
docs/sync.md, docs/data/sales_contract-columns.md (generated), docs/vps-setup.md, README.md
docs/phase0-verification.md            # CHANGED: V-4 note when the DBA acts
specs/001-foundation-data-layer/contracts/database-access.md   # AMENDED (dated)
```

**Structure Decision**: a separate top-level `sync/` service with its own image, so SQL Server drivers and credentials
never enter the `api` container; the API only reads and writes three small control tables, and the admin page is
extended in place. Every behaviour of the worker is behind a `Source` interface so the whole feature is tested with a
fake.
