---
description: "Task list for 001-foundation-data-layer"
---

# Tasks: Foundation Environment and Data Layer

**Input**: Design documents from `/specs/001-foundation-data-layer/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included. The spec's acceptance criteria are tests, and constitution Development Workflow requires failure-case tests for security-relevant code (roles, secrets).

**Organization**: Grouped by user story (US1 to US6 from spec.md). All paths are relative to the repository root.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 to US6, only in user-story phases
- **[MANUAL]**: needs the operator (VPS, Google, DNS); cannot be automated by the coding agent

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Repository skeleton and tooling

- [ ] T001 Verify Docker Engine 24+, Docker Compose v2 and `make` are installed on this dev machine (`docker --version`, `docker compose version`, `make --version`); if missing, stop and tell the operator what to install (do not install system packages silently)
- [ ] T002 Create directories `db/bootstrap/`, `db/migrations/`, `caddy/`, `backup/`, `scripts/`, `tests/foundation/`, `docs/`, `secrets/` with a `.gitkeep` in each empty one except `secrets/`
- [ ] T003 [P] Update `.gitignore`: add `secrets/*` and `!secrets/README.md`, `backups/`, `.pytest_cache/`, `tests/.venv/`; keep existing `.env` rules; verify with `git check-ignore -v secrets/postgres_superuser_password .env`
- [ ] T004 [P] Create `tests/pyproject.toml` (Python 3.12, deps `pytest`, `psycopg[binary]>=3`) and `tests/foundation/conftest.py` with fixtures that connect to Postgres at `127.0.0.1:${POSTGRES_PORT:-5432}` as each role (`agent_ro`, `sync_writer`, `app_owner`, `backup_ro`, superuser) using passwords read from `secrets/<role>_password`
- [ ] T005 Create `Makefile` with targets `secrets`, `up`, `up-prod`, `down`, `migrate`, `backup`, `restore-test`, `test`, `scan-secrets` exactly as listed in `specs/001-foundation-data-layer/contracts/operator-commands.md`; targets for not-yet-built scripts print "not implemented yet" and exit 1

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Secrets mechanism and Compose skeleton that every story uses

**⚠️ CRITICAL**: No user story work starts until this phase is complete

- [ ] T006 Create `scripts/gen-secrets.sh`: creates each missing file in `secrets/` with a 32-byte random value (`openssl rand -base64 32 | tr -d '\n'`) for `postgres_superuser_password`, `app_owner_password`, `sync_writer_password`, `agent_ro_password`, `backup_ro_password`; never overwrites an existing file; sets mode `600`; prints only file names, never values; does NOT create `backup_age_public_key` or `rclone_config` (operator-provided); wire to `make secrets`
- [ ] T007 [P] Create `secrets/README.md` listing every secret file name from data-model.md "Secret" table with its consumers, which two are operator-provided, and the warning that nothing else in `secrets/` is tracked
- [ ] T008 Create `compose.yaml` skeleton: one internal network `platform`, named volumes `pgdata`, `backup_local`, `caddy_data`, `caddy_config`, and top-level `secrets:` entries (file-based, `./secrets/<name>`) for all seven secrets in data-model.md; no services yet; no `env_file` anywhere (research R-5)

**Checkpoint**: `make secrets` works and `docker compose config` validates

---

## Phase 3: User Story 1 - One command brings up the base stack (Priority: P1) 🎯 MVP

**Goal**: `make up` starts Postgres, Redis (and Caddy with `make up-prod`) and reports health; data survives restart.

**Independent Test**: On a clean machine run `make secrets && make up`; all services healthy; `make down && make up` keeps data.

### Tests for User Story 1

- [ ] T009 [P] [US1] Write `tests/foundation/test_stack_health.py`: assert via `docker compose ps --format json` that `postgres` and `redis` are `healthy`; create a marker row (superuser, table `app.platform_meta` is not available yet so use a scratch table in a `scratch` schema), run `docker compose stop postgres && docker compose start postgres --wait`, assert the marker survives; assert Postgres port is bound to `127.0.0.1` only and Redis publishes no port

### Implementation for User Story 1

- [ ] T010 [US1] Add `postgres` service to `compose.yaml`: image `pgvector/pgvector:pg16`, `POSTGRES_PASSWORD_FILE=/run/secrets/postgres_superuser_password`, `POSTGRES_DB=${POSTGRES_DB:-platform}`, volume `pgdata`, secret `postgres_superuser_password` only, port `127.0.0.1:${POSTGRES_PORT:-5432}:5432` in the default profile, healthcheck `pg_isready -U postgres -d ${POSTGRES_DB:-platform}`, `restart: unless-stopped`
- [ ] T011 [P] [US1] Add `redis` service to `compose.yaml`: image `redis:7-alpine`, no published ports, no secrets, healthcheck `redis-cli ping` expecting `PONG`, `restart: unless-stopped`
- [ ] T012 [P] [US1] Create `caddy/Caddyfile`: site `{$PLATFORM_DOMAIN}` with `respond /healthz 200` and a plain-text placeholder for other paths; automatic HTTPS
- [ ] T013 [US1] Add `caddy` service to `compose.yaml` under `profiles: ["prod"]`: image `caddy:2`, ports `80:80` and `443:443`, volumes for `caddy/Caddyfile` (read-only), `caddy_data`, `caddy_config`, env `PLATFORM_DOMAIN`, no secrets, healthcheck `wget -qO- http://localhost/healthz` (or Caddy admin equivalent) so it reports health
- [ ] T014 [US1] Implement `make up` as `docker compose up -d --wait` and `make up-prod` as `docker compose --profile prod up -d --wait`, `make down` as `docker compose down`; fail non-zero if any service is unhealthy; run `make secrets` first if secret files are missing
- [ ] T015 [US1] Run `make secrets && make up && make test -- -k stack_health` locally; fix until green; commit
- [ ] T016 [US1] [MANUAL] Create `docs/vps-setup.md` (Ubuntu VPS): install Docker Engine and Compose plugin, `ufw` allowing only 22, 80, 443, clone repo over SSH, copy secrets, `make up-prod`; operator performs it on the VPS and confirms `dig +short vcp.onepercentdata.site` matches the VPS IP and `curl -fsS https://vcp.onepercentdata.site/healthz` returns 200 (quickstart Scenario 7)

**Checkpoint**: US1 independently demonstrable; MVP of the foundation

---

## Phase 4: User Story 2 - Isolated data areas and locked-down agent role (Priority: P1)

**Goal**: Three schemas, three extensions, four roles; `agent_ro` can only read `serving`, read-only by default, 30 s timeout.

**Independent Test**: `make test -- -k permissions` passes the whole matrix in `contracts/database-access.md`.

### Tests for User Story 2

- [ ] T017 [P] [US2] Write `tests/foundation/test_permissions.py` implementing every row of the permission matrix in `specs/001-foundation-data-layer/contracts/database-access.md` for all four roles (`✘` rows must raise `psycopg.errors.InsufficientPrivilege`), using a table `serving.test_items` created by `sync_writer` in a fixture and dropped afterwards; include temp-table creation, `CREATE ROLE`, `CREATE EXTENSION`, creating objects in `public`, and `agent_ro` running `SET transaction_read_only = off` then attempting an INSERT (must still fail)
- [ ] T018 [P] [US2] Write `tests/foundation/test_agent_session.py`: `agent_ro` has `SHOW default_transaction_read_only` = `on`, `SHOW statement_timeout` = `30s`, `SHOW search_path` = `serving`; `SELECT pg_sleep(31)` is cancelled with `QueryCanceled` between 29 and 33 seconds (mark `@pytest.mark.slow`)
- [ ] T019 [P] [US2] Write `tests/foundation/test_swap.py`: as `sync_writer`, create `serving.t__new`, then in one transaction rename `t` to `t__old`, `t__new` to `t`, drop `t__old`; assert `agent_ro` can `SELECT` from the swapped-in `serving.t` with no extra grant (guards the default-privileges design from data-model.md)
- [ ] T020 [P] [US2] Write `tests/foundation/test_extensions.py`: `vector`, `pg_trgm`, `citext` exist in `pg_extension`; `agent_ro` cannot see rows of `app` or `kb` tables and has no `USAGE` on those schemas

### Implementation for User Story 2

- [ ] T021 [US2] Create `db/bootstrap/10-extensions.sql`: `CREATE EXTENSION IF NOT EXISTS vector; ... pg_trgm; ... citext;` (idempotent)
- [ ] T022 [US2] Create `db/bootstrap/20-schemas-roles.sh` (POSIX sh, runs with `psql` as superuser): reads each password from `/run/secrets/<name>`; creates or updates (`ALTER ROLE ... PASSWORD`) `app_owner`, `sync_writer`, `agent_ro`, `backup_ro` as `LOGIN`, no `SUPERUSER`/`CREATEDB`/`CREATEROLE`; creates schemas `app` and `kb` owned by `app_owner` and `serving` owned by `sync_writer`; `GRANT pg_read_all_data TO backup_ro`; idempotent (safe to run on every start); never echoes passwords (use `psql -v` variables with `\set` from file contents, not shell interpolation into logged commands)
- [ ] T023 [US2] Create `db/bootstrap/30-privileges.sql` per research R-4 and data-model.md: `REVOKE ALL ON SCHEMA public FROM PUBLIC`; `REVOKE CONNECT ON DATABASE platform FROM PUBLIC` then `GRANT CONNECT` to the four roles; `REVOKE TEMPORARY ON DATABASE platform FROM PUBLIC`; `GRANT USAGE ON SCHEMA serving TO agent_ro`; `ALTER DEFAULT PRIVILEGES FOR ROLE sync_writer IN SCHEMA serving GRANT SELECT ON TABLES TO agent_ro`; `GRANT SELECT ON ALL TABLES IN SCHEMA serving TO agent_ro` (for pre-existing tables); `REVOKE ALL ON SCHEMA app, kb FROM agent_ro`; `ALTER ROLE agent_ro SET default_transaction_read_only = on`; `ALTER ROLE agent_ro SET statement_timeout = '30s'`; `ALTER ROLE agent_ro SET search_path = serving`; `ALTER ROLE agent_ro SET idle_in_transaction_session_timeout = '60s'` is NOT added (not in the contract)
- [ ] T024 [US2] Add `db-init` one-shot service to `compose.yaml`: image `pgvector/pgvector:pg16` (for `psql`), mounts `db/bootstrap/` read-only, secrets `postgres_superuser_password`, `app_owner_password`, `sync_writer_password`, `agent_ro_password`, `backup_ro_password`, `depends_on: postgres: condition: service_healthy`, `restart: "no"`, runs the three bootstrap files in order; `make up` waits for it to exit 0 (`--wait` treats a clean exit as success)
- [ ] T025 [US2] Run `make up` twice (bootstrap must be idempotent), then `make test -- -k "permissions or agent_session or swap or extensions"`; fix until green; commit

**Checkpoint**: US1 and US2 work together; the agent role guarantees are proven

---

## Phase 5: User Story 3 - Secrets stay out of the repository and unrelated services (Priority: P1)

**Goal**: No secret value in git or in a container that does not need it.

**Independent Test**: `make scan-secrets` exits 0 and `pytest -k secrets` passes.

### Tests for User Story 3

- [ ] T026 [P] [US3] Write `tests/foundation/test_secrets_scope.py`: for each service in `compose.yaml` (`docker compose config --format json`), assert its `secrets` list equals the expected set from data-model.md "Secret" table (`redis` and `caddy` have none, `postgres` only `postgres_superuser_password`); assert no service defines `env_file`; assert no service environment value contains any non-empty line from `secrets/*` or `.env`

### Implementation for User Story 3

- [ ] T027 [US3] Create `scripts/scan-secrets.sh`: collects values (length ≥ 8) from every file in `secrets/` and from `.env`; fails (non-zero) if any value appears in `git ls-files` content or in `git log -p --all`; never prints the matched value, only file path or commit hash; wire to `make scan-secrets`
- [ ] T028 [P] [US3] Create `.env.example` with the non-secret variables from `contracts/operator-commands.md` (`PLATFORM_DOMAIN=vcp.onepercentdata.site`, `POSTGRES_DB=platform`, `POSTGRES_PORT=5432`, `BACKUP_SCHEDULE=0 2 * * *`, `BACKUP_RETENTION_LOCAL_DAYS=7`, `BACKUP_REMOTE=gdrive:platform-backups`, `BACKUP_RETENTION_REMOTE_DAYS=30`) and commented names (no values) of the SQL Server variables used later (`sql_server_host`, `sql_server_port`, `sql_server_user`, `sql_server_password`, `sql_server_database`, `sql_server_tables`)
- [ ] T029 [US3] Verify `.env` and `secrets/*` are untracked (`git ls-files | grep -E '^(\.env|secrets/)'` shows only `secrets/README.md`), run `make scan-secrets` and `make test -- -k secrets`; fix and commit
- [ ] T030 [US3] [MANUAL] Operator: check which privileges the SQL Server account in `.env` has; if it can write, request a read-only account before the sync-worker feature (research R-5 follow-up). Record the outcome in `docs/phase0-verification.md` under V-1

**Checkpoint**: Secrets guarantees proven; all three P1 stories complete

---

## Phase 6: User Story 4 - Daily off-site backup with proven restore (Priority: P2)

**Goal**: Encrypted daily dump uploaded off the VPS; stale backup shows `unhealthy`; restore proven.

**Independent Test**: `make backup && make restore-test` passes with a local test remote and a throwaway age key.

### Tests for User Story 4

- [ ] T031 [P] [US4] Write `tests/foundation/test_backup_restore.py`: generate a throwaway age key pair in a temp dir; run the backup container once with `BACKUP_REMOTE` set to a local directory remote and that public key; assert a `platform-YYYYMMDD-HHMMSS.dump.age` file and a matching globals file exist both in the local volume and in the remote directory; decrypt with the private key, restore into an empty scratch database as superuser, and assert per-schema (`app`, `serving`, `kb`) table list and row counts match the live database
- [ ] T032 [P] [US4] Write `tests/foundation/test_backup_staleness.py`: with the `backup` service running, set its `last_success` file to 27 hours ago and assert `healthcheck.sh` exits non-zero and `docker compose ps` shows `unhealthy`; set it to 1 hour ago and assert healthy; assert a failed backup run (invalid remote) leaves `last_success` unchanged

### Implementation for User Story 4

- [ ] T033 [US4] Create `backup/Dockerfile` (Alpine with `postgresql16-client`, `rclone`, `age`, `busybox` crond) and `backup/entrypoint.sh` that renders the cron line from `BACKUP_SCHEDULE`, refuses to start (clear error) if `/run/secrets/backup_age_public_key` is missing, and runs `crond -f`
- [ ] T034 [US4] Create `backup/backup.sh`: `pg_dump -Fc` of `${POSTGRES_DB}` as `backup_ro` (password from `/run/secrets/backup_ro_password`) plus `pg_dumpall --globals-only` as `backup_ro` (roles without passwords is acceptable; passwords are re-synced by bootstrap on restore); pipe both through `age -R /run/secrets/backup_age_public_key`; name `platform-YYYYMMDD-HHMMSS.dump.age` and `platform-YYYYMMDD-HHMMSS.globals.sql.age`; copy to `BACKUP_REMOTE` with `rclone copy --config /run/secrets/rclone_config`; write ISO timestamp to `/var/lib/backup/last_success` only after the upload succeeds; delete local files older than `BACKUP_RETENTION_LOCAL_DAYS` (7) and remote files older than `BACKUP_RETENTION_REMOTE_DAYS` (30) with `rclone delete --min-age`; exit non-zero on any failure
- [ ] T035 [US4] Create `backup/healthcheck.sh`: exit 0 only if `/var/lib/backup/last_success` exists and is less than 26 hours old (see spec FR-013); a brand-new container with no backup yet is unhealthy after a 26-hour grace measured from container start
- [ ] T036 [US4] Add `backup` service to `compose.yaml`: build `./backup`, volume `backup_local` at `/var/lib/backup`, secrets `backup_ro_password`, `backup_age_public_key`, `rclone_config` only, env `BACKUP_SCHEDULE`, `BACKUP_REMOTE`, `BACKUP_RETENTION_LOCAL_DAYS`, `BACKUP_RETENTION_REMOTE_DAYS`, `POSTGRES_DB`, healthcheck calling `/healthcheck.sh` (interval 10 min), `depends_on: db-init: condition: service_completed_successfully`; add `secrets` entries `backup_age_public_key` and `rclone_config` as optional files so `make up` still works before the operator provides them (backup service stays `unhealthy` with a clear message until then)
- [ ] T037 [US4] Create `scripts/restore-test.sh` and wire `make backup` (runs `backup.sh` inside the running container) and `make restore-test`: takes the latest local backup, decrypts using the private key path in `AGE_IDENTITY_FILE`, restores into scratch database `restore_test`, compares table lists and row counts for `app`, `serving`, `kb` with the live database, drops the scratch database, exits 0 only on an exact match
- [ ] T038 [P] [US4] Create `docs/restore.md` with the five-step procedure from `contracts/operator-commands.md` expanded with exact commands, including how to decrypt with age, restore globals first, restore into an empty database, and re-run `make up`; add a "last tested" line the operator fills in after a real restore
- [ ] T039 [US4] Run `make test -- -k "backup"`; fix until green; commit
- [ ] T040 [US4] [MANUAL] Operator: create the age key pair (`age-keygen`), put the public key in `secrets/backup_age_public_key`, store the private key in a password manager; create a Google Cloud OAuth client and publish the consent screen (research R-6); on the VPS run `ssh -L localhost:53682:localhost:53682 <user>@<vps>` then `rclone config` to create remote `gdrive` (type `drive`, own client id), copy the resulting config to `secrets/rclone_config`; run `make backup` and confirm the file appears in Google Drive; run `make restore-test` once and fill the "last tested" line in `docs/restore.md`

**Checkpoint**: Backup and restore proven in test; production Drive setup blocked on T040

---

## Phase 7: User Story 5 - Repeatable schema migrations (Priority: P2)

**Goal**: Versioned SQL migrations apply only what is pending and are safe to re-run.

**Independent Test**: `make migrate` twice: both exit 0, second applies nothing.

### Tests for User Story 5

- [ ] T041 [P] [US5] Write `tests/foundation/test_migrations.py`: run `make migrate` twice; assert both exit 0 and the second output reports nothing applied; assert `app.platform_meta` exists with exactly one row where `key = 'schema_generation'` and `value = '1'`; add a temporary second migration file in a temp copy of `db/migrations`, run, assert only that one was applied; assert `agent_ro` cannot read `app.platform_meta`

### Implementation for User Story 5

- [ ] T042 [US5] Create `db/migrations/20261115000000_baseline.sql` (dbmate format with `-- migrate:up` and `-- migrate:down`): `CREATE TABLE IF NOT EXISTS app.platform_meta (key text PRIMARY KEY, value text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());` then `INSERT INTO app.platform_meta (key, value) VALUES ('schema_generation', '1') ON CONFLICT (key) DO NOTHING;`; down drops the table
- [ ] T043 [US5] Add `migrate` one-shot service to `compose.yaml`: image `amacneil/dbmate` (pin a version tag), mounts `db/migrations/` read-only, `DBMATE_MIGRATIONS_DIR=/db/migrations`, `DBMATE_NO_DUMP_SCHEMA=true`, `DATABASE_URL` built at start from `/run/secrets/app_owner_password` (no password in the Compose file), secret `app_owner_password` only, `depends_on: db-init: condition: service_completed_successfully`, `restart: "no"`; `make migrate` runs `docker compose run --rm migrate up`; `make up` includes it
- [ ] T044 [US5] Run `make test -- -k migrations`; fix until green; commit

**Checkpoint**: Later features can add tables by adding SQL files only

---

## Phase 8: User Story 6 - Phase 0 verification items are tracked (Priority: P3)

**Goal**: The four open `sales_contract` questions are recorded with owner, status, and a place for findings.

**Independent Test**: `docs/phase0-verification.md` has four rows with all columns; format check passes.

- [ ] T045 [US6] Create `docs/phase0-verification.md`: a table with columns `id`, `question`, `owner`, `status` (`open` / `in_progress` / `resolved`), `finding`, `date`; rows V-1 "Obtain read access to the source SQL Server (read-only account)", V-2 "Measure real row counts and delete-insert cycle of `dbo.sales_contract`", V-3 "Does the source delete-insert job run in a single transaction? (ask the table owner)", V-4 "Can the DBA enable snapshot isolation (`ALLOW_SNAPSHOT_ISOLATION`)?"; owner column filled with `TODO(owner)`, status `open`; add a note that the sync-worker feature must not start until all four are `resolved` (constitution, Development Workflow)
- [ ] T046 [P] [US6] Write `tests/foundation/test_verification_doc.py`: parse the table in `docs/phase0-verification.md`; assert exactly V-1 to V-4 exist, each has a non-empty question and a valid status value
- [ ] T047 [US6] [MANUAL] Operator: assign owners, ask the table owner and DBA, and record each finding in `docs/phase0-verification.md`; these block the sync-worker feature, not this one

**Checkpoint**: All six user stories complete

---

## Phase 9: Polish and Cross-Cutting Concerns

- [ ] T048 [P] Create `README.md` at the repository root: what the project is (two sentences), prerequisites, first-run steps (`cp .env.example .env`, `make secrets`, `make up`, `make test`), link to `docs/vps-setup.md`, `docs/restore.md`, and the spec folder
- [ ] T049 [P] Create `.github/workflows/ci.yml`: on push and pull request, run `make secrets`, `make up`, `make test` (excluding `slow` and anything needing real Drive), `make scan-secrets`, then `make down`; use only generated secrets, no repository secrets
- [ ] T050 Run every scenario in `specs/001-foundation-data-layer/quickstart.md` that does not need the VPS or Google Drive; tick off success criteria SC-001 to SC-007 in a short note at the end of `specs/001-foundation-data-layer/spec.md` under "Validation results" (include date and what is still blocked on MANUAL tasks)
- [ ] T051 Run `make scan-secrets` and confirm `git status` shows no `.env` or `secrets/` file staged; commit and push

---

## Dependencies and Execution Order

### Phase dependencies

- Phase 1 (Setup) → Phase 2 (Foundational) → all user stories
- **US1** needs only Phase 2
- **US2** needs US1 (`postgres` service) — it adds `db-init`
- **US3** needs US1 and US2 to have services to inspect (tests); scripts T027/T028 can start any time after Phase 2
- **US4** needs US2 (the `backup_ro` role and `db-init`)
- **US5** needs US2 (`app_owner` and the `app` schema)
- **US6** is independent after Phase 1 (documentation)
- Polish needs all desired stories

### Story completion order

`US1 → US2 → (US3, US4, US5 in parallel) → US6 anytime → Polish`

### Within each story

Tests first (written and failing), then implementation, then the "run and commit" task.

### Parallel opportunities

- T003, T004 (Setup); T007 (Foundational)
- T011 and T012 with T010 in US1 (different files, but `compose.yaml` edits must be merged sequentially)
- All four test files T017 to T020 in US2
- After US2: US3 (T026 to T029), US4 (T031 to T039) and US5 (T041 to T044) can be worked on in parallel by separate sessions if `compose.yaml` edits are merged carefully
- T045 and T046, T048 and T049

### Parallel example: User Story 2 tests

```text
T017 tests/foundation/test_permissions.py
T018 tests/foundation/test_agent_session.py
T019 tests/foundation/test_swap.py
T020 tests/foundation/test_extensions.py
```

## Implementation Strategy

1. **MVP**: Phase 1, Phase 2, US1. Result: one command brings up Postgres and Redis. Stop and validate.
2. **Safety core**: add US2 and US3. This is the minimum for any later feature to start (constitution I and II proven by tests).
3. **Operations**: add US4 and US5 in parallel. Backup is fully tested with a local remote; the Google Drive hookup (T040) is the operator's.
4. **Tracking and polish**: US6, README, CI, quickstart run.

**Manual tasks that block "production-ready" but not the code**: T016 (VPS and HTTPS), T030, T040 (age key, Google Drive), T047. Each is flagged `[MANUAL]`.

## Notes

- Commit after each "run and commit" task; push when a phase checkpoint passes.
- Never commit `.env` or anything under `secrets/` except `secrets/README.md`.
- Do not start the sync-worker feature before T047 is finished.
