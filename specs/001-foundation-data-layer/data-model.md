# Data Model: Foundation Environment and Data Layer

This feature defines structure and permissions, not business tables. Business tables (users,
conversations, topics, and the rest of spec §8) arrive with later features as migrations.

## Database layout

One database (`platform`) with three schemas and one migration-tracking table.

| Schema | Purpose | Owner | Written by | Read by |
| --- | --- | --- | --- | --- |
| `app` | Platform data (later: users, conversations, topics, audit) | `app_owner` | `app_owner` (later: API and Topic service via their own roles) | `app_owner`, `backup_ro` |
| `serving` | Copies of source tables | `sync_writer` | `sync_writer` only | `agent_ro`, `sync_writer`, `backup_ro` |
| `kb` | Document chunks and embeddings (later) | `app_owner` | `app_owner` | `app_owner`, `backup_ro` |
| `public` | Unused; no object may be created here | superuser | nobody | nobody |

Extensions installed in the database: `vector` (pgvector), `pg_trgm`, `citext`.

## Entities (from the spec)

### Role

| Role | Login | Can do | Cannot do |
| --- | --- | --- | --- |
| `app_owner` | yes | Own and migrate `app` and `kb`, create tables there | Touch `serving` data or create roles |
| `sync_writer` | yes | Create, replace, rename, drop tables in `serving`; write rows there | Read or write `app` and `kb` |
| `agent_ro` | yes | `SELECT` on every table in `serving` (current and future) | Anything else: write, DDL, temp objects, read `app` or `kb` |
| `backup_ro` | yes | Read everything (`pg_read_all_data`) for dumps | Write, DDL |

Settings on `agent_ro`: `default_transaction_read_only = on`, `statement_timeout = 30s`,
`search_path = serving`. Passwords come from secrets; none appear in SQL files.

State: roles are re-applied idempotently on every start (passwords re-synced from secret files), so
rotating a secret file and restarting the stack rotates the role password.

### Migration

| Field | Description |
| --- | --- |
| `version` | Timestamp-prefixed file name (for example `20261115000000_baseline`) |
| `applied_at` | When it ran |
| state | `pending` → `applied` (recorded by dbmate in `schema_migrations`); never edited after applying |

Baseline migration creates `app.platform_meta(key text primary key, value text not null, updated_at timestamptz not null default now())` and seeds `schema_generation = 1`. This exists to make idempotency testable.

### Secret

| Field | Description |
| --- | --- |
| name | File name under `secrets/` |
| consumer(s) | Services that declare it in `compose.yaml` |
| location | `secrets/<name>` on the host, `/run/secrets/<name>` in the container |

| Secret | Consumers |
| --- | --- |
| `postgres_superuser_password` | `postgres`, `db-init` |
| `app_owner_password` | `db-init`, `migrate` |
| `sync_writer_password` | `db-init` (the future sync worker will also declare it) |
| `agent_ro_password` | `db-init` (the future Topic service will also declare it) |
| `backup_ro_password` | `db-init`, `backup` |
| `backup_age_public_key` | `backup` (public key only) |
| `rclone_config` | `backup` |

Source SQL Server credentials are not consumed by any service in this feature.

### Backup

| Field | Description |
| --- | --- |
| `name` | `platform-YYYYMMDD-HHMMSS.dump.age` (plus a matching `globals` file) |
| `location` | Local volume (7 days) and the operator's off-site remote |
| `status` | Written as `last_success` timestamp; stale after 26 hours means unhealthy |

State: `scheduled → running → succeeded | failed`. A failed run leaves `last_success` unchanged, so the health check turns unhealthy once it ages out.

### Verification Item

Tracked as a table in `docs/phase0-verification.md` (not in the database).

| Field | Description |
| --- | --- |
| `id` | V-1 to V-4 |
| `question` | The open question about the source system |
| `owner` | Person responsible for getting the answer |
| `status` | `open` → `in_progress` → `resolved` |
| `finding` | The recorded answer, with date |

Items: V-1 read access to the source SQL Server; V-2 real row counts and delete-insert cycle of `sales_contract`; V-3 whether the source job runs in a single transaction; V-4 whether the DBA can enable snapshot isolation.

## Relationships and rules

- `agent_ro` privileges on `serving` are inherited by future tables through `ALTER DEFAULT PRIVILEGES FOR ROLE sync_writer IN SCHEMA serving GRANT SELECT ON TABLES TO agent_ro`. This covers the rename-based atomic swap, because the swapped-in table is created by `sync_writer`.
- No role other than the superuser can create roles or extensions.
- A table in `serving` is never directly writable by anything except `sync_writer` (FR-004).
- `app` and `kb` have no grant to `agent_ro` (FR-007); the privileges are revoked, not merely not granted, so a later accidental default grant is caught by the test suite.
