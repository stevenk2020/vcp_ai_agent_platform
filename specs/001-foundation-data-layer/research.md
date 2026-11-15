# Research: Foundation Environment and Data Layer

All Technical Context items were resolved from the constitution, the technical spec, and the choices
below. No `NEEDS CLARIFICATION` items remain. Items marked *operator input* need a value at
implementation time but do not change the design.

## R-1: Postgres image and extensions

- **Decision**: `pgvector/pgvector:pg16`. `pg_trgm` and `citext` are contrib extensions already in the image; enable all three with `CREATE EXTENSION IF NOT EXISTS` in the bootstrap step.
- **Rationale**: One image gives all required extensions at Postgres 16, as fixed by the constitution. No custom image to maintain.
- **Alternatives**: Build from `postgres:16` and compile pgvector (more maintenance, slower build); Postgres 17 (not the constitution's version; revisit later).

## R-2: One-command startup and health

- **Decision**: Plain Compose with healthchecks and `depends_on: condition: service_healthy`; `make up` runs `docker compose up -d --wait`. Production adds the `prod` profile (`make up-prod`) which also starts Caddy.
- **Rationale**: `--wait` blocks until every service is healthy and fails loudly otherwise, which is exactly the acceptance test for FR-001/FR-002. Profiles avoid a second Compose file.
- **Alternatives**: Separate `compose.prod.yaml` override (two files to keep in sync); systemd units (not portable to dev machines); Kubernetes (overkill, violates principle IV).

## R-3: Schemas, roles, and who runs what

- **Decision**: An idempotent *bootstrap* step runs as superuser on every `up` and creates extensions, schemas, roles, and grants, reading role passwords from secret files. *Migrations* (dbmate) run afterwards as `app_owner` and only create tables in `app` and `kb`. Roles: `app_owner` (owns `app`, `kb`), `sync_writer` (owns `serving`; the future sync worker), `agent_ro` (read-only on `serving`), `backup_ro` (member of `pg_read_all_data`, used only by the backup job).
- **Rationale**: Role passwords must never be in migration files committed to git, so anything needing a secret or a superuser goes through the bootstrap, which can read `/run/secrets`. Making `sync_writer` the owner of `serving` lets `ALTER DEFAULT PRIVILEGES FOR ROLE sync_writer IN SCHEMA serving GRANT SELECT ... TO agent_ro` cover tables created by the future snapshot + atomic swap (spec §6.2) with no per-table grants.
- **Alternatives**: Put roles in migrations with passwords templated in (secrets end up in migration state or logs); let one superuser own everything (breaks least privilege).

## R-4: Hardening the agent role

- **Decision**: For `agent_ro`: `ALTER ROLE ... SET default_transaction_read_only = on`, `SET statement_timeout = '30s'`, `SET search_path = serving`; `REVOKE ALL` on schemas `app`, `kb`, `public`; `REVOKE CONNECT ... FROM PUBLIC` then `GRANT CONNECT` only to the named roles; `REVOKE TEMPORARY ON DATABASE ... FROM PUBLIC`; no `CREATE` anywhere; no membership in any other role.
- **Rationale**: Privileges (not settings) are the real barrier: even if the role switches its session to read-write it still has no write privilege on `serving` and cannot create temp objects. The settings satisfy the acceptance criteria (default read-only, 30 s cancel).
- **Known residual risks, accepted**: (a) a session can `SET statement_timeout` or `SET transaction_read_only = off` for itself; the SQL validator (later feature, constitution II) rejects `SET` and any non-SELECT statement. (b) The role can see table and column names in the system catalog; names are visible but data is not. Both are documented in [contracts/database-access.md](contracts/database-access.md).
- **Alternatives**: Run the agent against a streaming replica (more infrastructure than 50 users need); PgBouncer with enforced settings (extra service).

## R-5: Secrets handling

- **Decision**: Docker Compose file-based `secrets:` sourced from gitignored `secrets/<name>` files and mounted at `/run/secrets/<name>` only into the services that declare them. `scripts/gen-secrets.sh` creates missing files with random values. `.env` stays gitignored and holds only local settings; no service uses `env_file: .env`, so the source SQL Server credentials in it reach no container in this feature. `.env.example` lists names without values. `scripts/scan-secrets.sh` checks that no value from `secrets/` or `.env` appears in tracked files or git history.
- **Rationale**: File mounts keep values out of `docker inspect` environment output and out of unrelated services, satisfying FR-008/FR-009. Compose secrets need no extra tooling.
- **Alternatives**: Environment variables (visible via `docker inspect` and to any process); Docker Swarm secrets, Vault, SOPS (more machinery than one developer needs now; SOPS can be added later for team sharing).
- **Follow-up**: Prefer a read-only SQL Server account for the sync worker; check the privileges of the current account before the sync feature.

## R-6: Backup and restore

- **Decision**: A `backup` container runs a daily job (busybox `crond`): `pg_dump -Fc` of the application database plus `pg_dumpall --globals-only` (roles), encrypted with `age` using the operator's public key, uploaded with `rclone` to the operator's remote, local copy retained 7 days. Success is recorded as a timestamp file; the container healthcheck fails if the last success is older than 26 hours, so a missing or stale backup shows as `unhealthy` in `docker compose ps` (FR-013). Restore is a documented manual procedure; `scripts/restore-test.sh` restores the latest backup into a scratch database and compares per-schema row counts with the live database. Only the age *public* key lives on the VPS; the private key stays with the operator.
- **Rationale**: Backups carry business data off the machine, so encrypting them protects against a leaked bucket. A real restore into an empty database is the only proof a backup works (FR-012).
- **Alternatives**: WAL archiving / pgBackRest / Barman (point-in-time recovery is more than the current scale needs; revisit if the data becomes irreplaceable); VPS provider snapshots only (not tested restore, same provider failure domain).
- **Operator choice (2026-11-15)**: off-site destination is **Google Drive**, accessed through an rclone `drive` remote (for example `gdrive:platform-backups`). Practical points that shape the implementation:
  - Authorisation needs a browser, but the VPS can stay headless. Option 1 (preferred): `ssh -L localhost:53682:localhost:53682 <user>@<vps>` from the operator's machine, then `rclone config` on the VPS and answer *yes* to browser authentication; the Google callback to `localhost:53682` travels through the tunnel. Option 2: answer *no* and run `rclone authorize "drive"` on a machine with a browser, then paste the token. Either way the result lands in `secrets/rclone_config`.
  - Use **your own Google OAuth client ID** (or a service account with a shared folder) instead of rclone's shared one, to avoid rate limits. If the OAuth consent screen is left in "Testing" status, Google expires the refresh token after 7 days and backups would silently start failing; publish the app (Internal/Production) or use a service account. The staleness health check (26 h) catches this, but it should not be a surprise.
  - Backups are encrypted with `age` before upload, so Drive only ever holds ciphertext.
  - Retention at the remote is handled by the backup job (`rclone delete --min-age`), default 30 days; Drive's trash is emptied by the same job.
- *Operator input still open*: the age key pair (deferred by the operator; the backup job refuses to run without a public key, so `make backup` stays unavailable until it exists).

## R-7: Migrations

- **Decision**: `dbmate` in a one-shot container (`migrate` service), plain SQL files in `db/migrations`, tracked in its own `schema_migrations` table; `make migrate` and the normal `up` run it. One baseline migration creates a small `app.platform_meta` key/value table so idempotency is demonstrable.
- **Rationale**: Re-runnable, applies only pending migrations, no ORM needed for this layer, and later features add SQL files without touching tooling. Works for Python or any other service.
- **Alternatives**: Alembic (needs a Python project and ties the data layer to the API package); Flyway (JVM image); hand-run SQL scripts (fails FR-014).

## R-8: HTTPS entry point

- **Decision**: Caddy in the `prod` profile with automatic certificates for `PLATFORM_DOMAIN`; placeholder site with `/healthz` returning 200 until the API exists. Locally the entry point is skipped.
- **Rationale**: Caddy is already the constitution's choice and handles certificate issuance and renewal with no extra configuration.
- **Operator choice (2026-11-15)**: `PLATFORM_DOMAIN=vcp.onepercentdata.site`. The operator already created the DNS record pointing at the VPS IP. Requirements: ports 80 and 443 open in the firewall (Caddy uses them for certificate issuance and renewal). The domain can be changed later by editing one variable.
- *To verify at implementation time*: the record resolves to the VPS (`dig +short vcp.onepercentdata.site`) and ports 80/443 are reachable.

## R-9: Network exposure

- **Decision**: One internal Compose network. In production only Caddy publishes ports 80 and 443. In development, Postgres is published on `127.0.0.1` only so tests and psql can reach it; the VPS profile does not publish it. Redis is never published.
- **Rationale**: Principle III and the technical spec require internal-only services; a localhost-only dev port keeps testing simple.

## R-10: Tests

- **Decision**: pytest + psycopg 3, run with `uv run` against the live stack. Tests: (1) `agent_ro` permission matrix including all forbidden operations and temp-table creation; (2) a 31-second `pg_sleep` is cancelled; (3) `sync_writer` can create and swap a table in `serving` and `agent_ro` can read it immediately (guards the future atomic swap); (4) migrations applied twice; (5) backup then restore round trip; (6) `scan-secrets.sh` returns clean.
- **Rationale**: The constitution requires failure-case tests for security-relevant code; these tests are the acceptance suite from the spec.
