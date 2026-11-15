# Feature Specification: Sales Contract Sync (source table copy into the platform)

**Feature Branch**: `dev_spec_04`

**Created**: 2026-11-15

**Status**: Draft

**Input**: User description: "Sync worker that copies the SQL Server source table dbo.sales_contract into the platform PostgreSQL database so that later topic features can query it quickly and safely. Full replace only, scheduled about every 10 minutes and on demand, one consistent read of the source, atomic replacement of the copy, sanity guards, sync log and freshness record, retries, read-only source access, faithful column types, migration, tests with a fake source, docs and runbook." (full text in the conversation that created this feature)

Cited sources: the architectural spec "AI Agent Platform — Review & Technical Spec.md" (sync and data sections), constitution Principle II (staging and atomic swap, row-count sanity check, no `NOLOCK`) and its rule that sensitive columns are flagged and masked or excluded, `docs/topics/sales_contract.md` (column facts and traps), `docs/phase0-verification.md` (V-2 answered: about every 10 minutes, about 2 minutes per run; V-3 answered: one commit; V-4 pending: source snapshot isolation is still OFF).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A fresh, consistent copy of the source table (Priority: P1)

The platform holds its own copy of the `sales_contract` source table that is refreshed about every 10 minutes. Whoever queries the copy (later: the topic service on behalf of a chat user) always sees one complete state of the source: never an empty table, never a half-loaded one, never rows from two different source states mixed together.

**Why this priority**: Without a correct copy there is nothing for later topic features to query. The source job deletes and re-inserts the whole table about every 10 minutes (about 2 minutes per run, one commit), so a naive copy would sometimes show an empty or partial table and produce confident but wrong numbers.

**Independent Test**: With a fake source holding known rows, run one sync and compare the copy with the source row by row (all columns, nulls and empty strings, money, dates, Vietnamese text); then change the fake source, run again while a reader polls the copy continuously, and check the reader only ever saw the old complete state or the new complete state.

**Acceptance Scenarios**:

1. **Given** a source table with N rows and an empty copy, **When** a sync runs, **Then** the copy has the same N rows, the same columns and values, and a successful run is recorded with N source rows and N copied rows.
2. **Given** a copy of an earlier source state, **When** the source changes (rows added, removed and updated) and a sync runs, **Then** the copy equals the new source state and rows that no longer exist in the source are gone.
3. **Given** a reader querying the copy continuously, **When** a sync replaces the copy, **Then** every read returns either the complete previous state or the complete new state, never zero rows and never a partial count.
4. **Given** the source is in the middle of its own delete-and-insert (read blocked until the source commits), **When** a sync starts, **Then** it waits for the source's commit within a configured limit and then reads the new complete state.
5. **Given** source values that are null, empty strings, very small or large amounts, fractional prices, Vietnamese text with diacritics, and dates, **When** they are copied, **Then** each is stored without rounding, truncation, or change of meaning (empty string stays empty, null stays null).

---

### User Story 2 - Sanity guards keep the last good copy (Priority: P1)

If the source answers with something that is clearly wrong (no rows at all, or far fewer rows than before), the sync refuses to replace the copy, keeps the previous good data, and records why.

**Why this priority**: A silently emptied or truncated copy is the highest-risk failure of this feature (wrong answers that sound right). The guard is what makes "never an empty table" true even when the source misbehaves.

**Independent Test**: Feed a fake source that returns zero rows, then one that returns 40% of the current rows, then one below the configured minimum, then a normal one; verify the copy is untouched by the first three, each failure is recorded with its reason, and the fourth succeeds.

**Acceptance Scenarios**:

1. **Given** a copy with 35,000 rows, **When** the source returns zero rows, **Then** the copy is unchanged and the run is recorded as `refused` with the reason "source returned no rows".
2. **Given** a copy with 35,000 rows and a configured minimum fraction of 50%, **When** the source returns fewer than 17,500 rows, **Then** the copy is unchanged and the run is recorded as `refused` with both counts.
3. **Given** an empty copy (first ever sync), **When** the source returns fewer rows than the configured absolute minimum, **Then** the copy stays empty and the run is `refused`.
4. **Given** the guard thresholds are changed in configuration, **When** the next run happens, **Then** the new thresholds apply without any code change.
5. **Given** a run was refused, **When** the source later returns a normal state, **Then** the next run succeeds and replaces the copy.

---

### User Story 3 - Everyone can see how fresh the data is (Priority: P2)

An admin (and later feature code, such as the topic service which must show "data as of" with every answer) can see when the copy was last successfully refreshed, whether the latest run succeeded, and a short history of runs with counts, duration and error text.

**Why this priority**: The constitution requires every data answer to show `data_as_of`; the admin needs to know if the numbers are stale. Not needed to produce a copy, but needed before anyone trusts it.

**Independent Test**: After a successful run, a failed run and a refused run, ask for the freshness and the history and check that they show the last success time, the latest outcome, and the three runs with start, end, outcome, counts, duration and error text (and no secret).

**Acceptance Scenarios**:

1. **Given** at least one successful run, **When** an admin or later feature asks for freshness, **Then** it returns the time of the last successful read ("data as of"), the outcome of the latest run, and the age of the data.
2. **Given** runs with different outcomes, **When** an admin lists the history, **Then** newest first, each shows start, end, outcome (`succeeded`, `failed`, `refused`), source row count, copied row count, duration, and error text.
3. **Given** the last success is older than a configured staleness threshold (default 30 minutes), **When** freshness is requested, **Then** it is marked stale.
4. **Given** a person who is not an approved admin, **When** they ask for sync history, **Then** they are refused (freshness for later features is available to approved users through the topic feature, not from this feature's admin pages).
5. **Given** no run has ever succeeded, **When** freshness is requested, **Then** it says there is no data yet instead of a time.

---

### User Story 4 - An admin can force a sync, never two at once (Priority: P2)

An admin can trigger a sync on demand (for example right after the source owner confirms a correction). Only one sync runs at a time: a second request while one is running is refused or merged with the running one, and the scheduled run never overlaps either.

**Why this priority**: Operations need a manual lever and a guarantee against overlapping runs, which would double the load on the source and could race on the replacement.

**Independent Test**: Start a slow fake sync, request a second one, and check only one reads the source; check that a manual trigger by an admin is accepted and audited and that a non-admin is refused.

**Acceptance Scenarios**:

1. **Given** no sync is running, **When** an admin triggers one, **Then** a run starts at once and the trigger (who, when) is audited.
2. **Given** a sync is running, **When** an admin triggers another or the schedule fires, **Then** no second run starts and the requester is told one is already running.
3. **Given** a person who is not an approved admin, **When** they try to trigger a sync, **Then** it is refused and nothing runs.
4. **Given** the platform restarts in the middle of a run, **When** it comes back, **Then** the unfinished run is recorded as failed, the copy is the previous complete state, and the next scheduled run works (no stuck lock).

---

### User Story 5 - Source problems degrade safely and visibly (Priority: P2)

When the source is unreachable, slow, or rejects the login, the sync retries with growing waits, records each failure, switches to a clear "failing" state after repeated failures, keeps serving the previous copy (marked stale once old enough), and recovers by itself when the source is back. Error records never contain passwords or connection secrets.

**Why this priority**: The source is an operational system owned by someone else and will have outages and maintenance; the platform must not corrupt its copy or hide the problem.

**Independent Test**: Make the fake source fail with connection errors, slow responses beyond the read limit, and authentication errors; check the retries, the recorded errors (no secrets), the failing state after the configured number of consecutive failures, and recovery.

**Acceptance Scenarios**:

1. **Given** the source is unreachable, **When** a run starts, **Then** it retries with growing waits up to a configured number of attempts, then records `failed` with a readable reason, and the previous copy is untouched.
2. **Given** a read that exceeds the configured read limit (default 5 minutes), **When** the limit passes, **Then** the run is cancelled and recorded as `failed` (timeout) and the next scheduled run starts normally.
3. **Given** three consecutive failed or refused runs (configurable), **When** freshness is requested, **Then** the state is "failing" with the latest reason.
4. **Given** the source comes back, **When** the next run succeeds, **Then** the state returns to healthy by itself.
5. **Given** any failure, **When** its error text, the logs and the sync history are inspected, **Then** none contains the source password, the source user name with the password, or a full connection string.

---

### User Story 6 - The source is only read, with a safe account (Priority: P3)

The sync only reads from the source. At start it checks that the source account is not a high-privilege account and refuses to run if it is; it never writes, never locks the source with hints that block the source job, and never uses uncommitted reads.

**Why this priority**: Protecting the operational database is a standing rule (constitution Principle II). A mistake here is rare but severe, so it is checked automatically, but it does not add user-visible capability.

**Independent Test**: Run the start check against a fake source reporting a sysadmin or db_owner account and against one reporting a read-only account; check that the first stops the worker with a clear message and the second runs. Check the statements the worker sends to the source are all reads.

**Acceptance Scenarios**:

1. **Given** the source account has administrator or database-owner rights, **When** the worker starts, **Then** it refuses to run, says why, and performs no sync.
2. **Given** a read-only source account, **When** the worker starts, **Then** it runs normally.
3. **Given** a full run, **When** every statement sent to the source is inspected, **Then** all are reads of the configured table, none uses uncommitted-read hints.
4. **Given** the source administrator later enables consistent-snapshot reading on the source, **When** the operator turns on the corresponding setting, **Then** reads stop waiting for the source's own job and no code change is needed.

---

### Edge Cases

- The source returns the same `(entity, row_id)` twice (duplicate natural key): the run is refused with a clear reason (the copy would be ambiguous) and the previous copy stays.
- The source removes a column the copy needs, or changes its type: the run is refused with the reason "source shape changed" and lists the difference; the previous copy stays. The source only *adding* a column is harmless: the column is not copied (see below), and the run records a warning naming it.
- A source value does not fit the copy's column (text too long, number out of range): the run is refused, not truncated.
- The source read succeeds but the replacement fails halfway (disk full, connection lost): the previous complete copy remains and the run is `failed`.
- Two platform instances start at the same time (restart, accidental double start): only one sync runs at a time.
- The source clock or the platform clock moves: freshness uses the platform's own clock for "data as of"; source `update_time` values are copied as data only.
- A source column marked as excluded (the salesperson columns) is never selected from the source; if the source drops or renames it, the run is not refused for that reason.
- A new sensitive column appears in the source: it is not copied (the copy only holds columns named in the copy definition) until someone decides.
- Backups taken during a replacement contain either the old complete state or the new one, and a restored backup still works with the next sync.
- The source has zero rows legitimately (for example during a deliberate wipe by its owner): the copy is kept until an admin confirms by changing the guard, so this is a documented operator action, not an automatic one.
- A manual trigger arrives within seconds of a scheduled run: it is treated like any request during a run (refused as already running), not queued forever.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST refresh a copy of `dbo.sales_contract` in the platform database on a schedule of about every 10 minutes (configurable), as a full replace of the whole table.
- **FR-002**: The system MUST NOT run two syncs of the same table at the same time, whether started by the schedule, by an admin, or after a restart; a request during a run MUST be told that one is already running.
- **FR-003**: Each run MUST read the whole source table in one consistent read: when the source offers consistent snapshot reading and the setting is on, using it; otherwise waiting for the source's own commit up to a configurable read limit (default 5 minutes, always longer than the source job's observed 2 minutes). It MUST NOT use uncommitted-read hints.
- **FR-004**: The system MUST replace the copy atomically: readers see either the complete previous state or the complete new state, never an empty or partial table, including while the replacement is in progress.
- **FR-005**: Before replacing, the system MUST apply guards and refuse to replace (keeping the previous copy) when the source returns zero rows, fewer rows than a configurable fraction (default 50%) of the current copy, fewer than a configurable absolute minimum, duplicate natural keys, or a changed column set or type; each refusal MUST be recorded with its reason and the counts.
- **FR-006**: The system MUST keep a sync log with, for every run: start time, end time, outcome (`succeeded`, `failed`, `refused`), source row count, copied row count, duration, who or what triggered it (schedule or admin), and an error text that contains no secrets.
- **FR-007**: The system MUST keep a freshness record per synced table with the time of the last successful read ("data as of"), and expose it to later features and to admins, including an indication of staleness (default threshold 30 minutes, configurable) and of a "failing" state after a configurable number of consecutive failed or refused runs (default 3).
- **FR-008**: The system MUST provide an admin-only way to list recent sync runs and to read freshness, and an admin-only way to trigger a sync on demand; both MUST go through the platform's existing authenticated, Origin-checked admin interface, and the trigger MUST be audited (who, when, outcome).
- **FR-009**: On source connection errors the system MUST retry with growing waits up to a configurable number of attempts, then record the failure, keep the previous copy, and try again at the next scheduled time; it MUST recover by itself when the source returns.
- **FR-010**: The system MUST NOT write secrets (source password, connection string, platform database passwords) to logs, to the sync log, to error texts, or to responses.
- **FR-011**: The copy MUST hold the same columns as the source, except the excluded sensitive columns (FR-020) with faithful values: nulls stay null, empty text stays empty, text keeps its characters, dates stay dates, date-times keep their precision and carry no assumed time zone, and money and quantities are stored as exact decimal numbers; the exact number format and the rule applied to the source's floating-point money values MUST be decided in the plan and documented in the column dictionary.
- **FR-012**: The copy MUST have `(entity, row_id)` as its unique key and indexes that support the likely filters (entity, confirmed date, finished date, customer code, goods code `code_old`, `cat_1`, `cat_2`).
- **FR-013**: Only the sync identity MUST be able to write to the copy; the agent's read-only identity MUST only read it and MUST NOT see anything outside the copy's area; this MUST be proven by tests of both identities.
- **FR-014**: At start, and before each run, the system MUST verify the source account is not an administrator or database owner and MUST refuse to run if it is; the worker MUST only send reads to the source.
- **FR-015**: The table list, schedule, guards, read limit, retry counts and the snapshot-reading switch MUST be configuration, not code constants; adding another source table later MUST need configuration plus a copy definition, not a rewrite of the worker (only `sales_contract` is in scope now).
- **FR-016**: The structure of the copy and of the sync control records MUST be created by versioned, idempotent definitions applied automatically (by the sync identity, which is the only one allowed to create objects in the copy's area; plan R-4) and covered by the foundation tests; the existing backup and restore MUST keep working and include the copy and the sync log.
- **FR-017**: A column dictionary for the copy MUST exist, listing each column with its meaning, type, nullability, and a sensitivity flag, consistent with `docs/topics/sales_contract.md`.
- **FR-018**: Every behavior above MUST be verifiable with a fake source: the automated tests MUST NOT need a real SQL Server.
- **FR-019**: The feature MUST ship operator documentation: how to check freshness, how to force a sync, what to do when the source is down, when a guard refuses, when the source shape changes, and how to turn on snapshot reading after the DBA enables it.
- **FR-020**: The system MUST treat sensitive columns according to the constitution: they are flagged in the column dictionary and masked or excluded from the copy. Decision (operator, 2026-11-15, option A): the salesperson columns `employee_code_bdd` and `employee_name_bdd` are **excluded** from the copy (staff personal data, and the name is missing in 54% of rows); the customer address columns `customer_address` and `address` are **kept** and flagged as business addresses. The excluded columns are never read from the source, never stored, and never logged; the column dictionary lists them as excluded with the reason. Adding them back later needs a deliberate decision, a constitution-compliant masking plan, and a new migration.

### Key Entities *(include if feature involves data)*

- **Synced table (copy)**: the platform's own copy of one source table, holding exactly one complete state of it at any time; key `(entity, row_id)`.
- **Sync run**: one attempt to refresh a copy: when it started and ended, what triggered it, outcome, counts, duration, error text.
- **Freshness record**: per synced table, the time of the last successful read, the latest outcome, the count of consecutive failures, and the derived state (healthy, stale, failing, no data yet).
- **Source definition (configuration)**: which source table, which columns, schedule, guards, read limit, retries, snapshot-reading switch.
- **Column dictionary**: the documented list of columns of the copy with meaning, type, and sensitivity flag.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a change in the source, the copy reflects it within 15 minutes (one source cycle plus one sync run) in normal operation.
- **SC-002**: A full sync of the current source size (about 35,000 rows, 66 columns) completes within 5 minutes when the source is not blocked.
- **SC-003**: During 1,000 consecutive reads of the copy taken while it is being replaced, zero reads see an empty table or a row count that differs from both the previous and the new complete state.
- **SC-004**: In every tested bad-source case (zero rows, below the fraction, below the minimum, duplicate keys, changed columns, unreachable, timeout, login refused), the previous copy is 100% unchanged and the run is recorded with a readable reason.
- **SC-005**: An admin can find out when the data was last refreshed and whether the latest run succeeded in at most 2 clicks or one request, and the answer is correct to the minute.
- **SC-006**: No test or log inspection finds a source password or connection secret in any log, sync record, error text or response.
- **SC-007**: After a source outage of one hour, the platform resumes by itself within one schedule interval of the source's return, with no operator action.
- **SC-008**: Turning the snapshot-reading switch on needs no code change and no data migration.

## Assumptions

- Only `dbo.sales_contract` is synced now; the source rebuilds it about every 10 minutes (about 2 minutes, one commit) and has about 35,000 rows and 66 nullable columns, with `(entity, row_id)` unique (V-2, V-3 answered; verified on 2026-11-15).
- The source's consistent snapshot reading is OFF (V-4 pending with the DBA). Until it is ON, a read can wait up to about 2 minutes for the source's commit; the platform tolerates this with a read limit of 5 minutes. A scheduled run that is still waiting does not overlap with the next one.
- "Data as of" means the time the consistent read of the source started (platform clock), not a value from the source.
- Full replace is enough at this size; incremental or change-capture sync is out of scope.
- The existing platform data layer (feature 001) provides the area for copies, the write-only-for-sync identity, the read-only agent identity, migrations and backups; the sync area is the one named for copies in that layer.
- The source account in `.env` is read-only (V-1 resolved); the start check is an additional guard, not a replacement for correct source permissions.
- Admin access to run history, freshness and manual trigger reuses the existing admin area and API rules (feature 002/003); no new roles.
- Mail stays disabled; there is no alert by email. A failing state is visible in the admin area and in the logs.
- The copy therefore has 64 of the source's 66 columns; questions about salespeople cannot be answered from it (the knowledge base must say so when the topic feature is built).
- Out of scope: the Topic MCP server and query tool, serving the knowledge base, the chat UI, the agent loop, the LLM gateway, other source tables, writing back to the source.
