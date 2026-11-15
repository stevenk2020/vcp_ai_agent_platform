# Contract: Database Access

What every later feature may rely on from the data layer. Changing any row of this contract is a
breaking change and needs a constitution-level review (principles I and II).

## Connection

| Item | Value |
| --- | --- |
| Host (inside Compose network) | `postgres` |
| Port | `5432` (not published in production; `127.0.0.1` only in development) |
| Database | `platform` |
| Credentials | Role name as below; password from `/run/secrets/<role>_password` |
| TLS | Not required on the internal network |

## Permission matrix

`✔` allowed, `✘` must fail with a permission error. This table is the acceptance test.

| Operation | `agent_ro` | `sync_writer` | `app_owner` | `backup_ro` | `api_rw` |
| --- | --- | --- | --- | --- | --- |
| SELECT in `serving` | ✔ | ✔ | ✘ | ✔ | ✘ |
| INSERT / UPDATE / DELETE / TRUNCATE in `serving` | ✘ | ✔ | ✘ | ✘ | ✘ |
| CREATE / ALTER / DROP / RENAME table in `serving` | ✘ | ✔ | ✘ | ✘ | ✘ |
| SELECT in `app` or `kb` | ✘ | ✘ | ✔ | ✔ | table-level (see below) |
| Any write or DDL in `app` or `kb` | ✘ | ✘ | ✔ | ✘ | table-level (see below) |
| CREATE anything in `public` | ✘ | ✘ | ✘ | ✘ | ✘ |
| CREATE TEMP table | ✘ | ✘ | ✘ | ✘ | ✘ |
| CREATE ROLE / CREATE EXTENSION | ✘ | ✘ | ✘ | ✘ | ✘ |

## Session behaviour of `agent_ro`

| Behaviour | Guarantee | Enforced by |
| --- | --- | --- |
| Default transaction mode | Read-only | Role setting |
| Statement duration | Cancelled after 30 s by default | Role setting |
| Visible schema by default | `serving` | Role `search_path` |
| Cross-schema reads | Rejected | Revoked privileges |

**Limits of this contract** (closed by later features, tracked here so they are not forgotten):

- A session can override its own `statement_timeout` or read-only default with `SET`. Privileges still
  block all writes. The Topic service's SQL validator MUST reject `SET` and every statement that is
  not a single SELECT (constitution II).
- The system catalog exposes table and column names of other schemas, not data.

## Rules for future features

1. New tables in `app` and `kb` are created by migrations run as `app_owner`; they inherit no access for `agent_ro`.
2. New tables in `serving` are created only by `sync_writer`; `agent_ro` gets `SELECT` automatically.
3. A service that needs different access gets its own role added in the bootstrap step with its own secret; it never reuses another role's password.
4. Role passwords are never written to SQL files, migrations, logs, or git.

## Role added by feature 002 (`api_rw`)

`api_rw` is the API service's role. It has `USAGE` on schema `app` and only the table rights granted by
migrations: `SELECT, INSERT, UPDATE, DELETE` on `app.users`, `app.allowlist`, `app.email_codes`,
`app.sessions`, and only `SELECT, INSERT` on `app.audit_events` (append-only). It has no access to `serving`,
`kb` or `public`, no default privileges, no role settings. See
`specs/002-passwordless-auth/data-model.md` for the table list.

## Amendment 2026-11-15 (feature 004, sync worker)

- `api_rw` gains `USAGE` on schema `serving` (bootstrap), and explicit table rights granted by the worker's
  `sync/sql/control.sql`: `SELECT` on `serving.sync_state` and `serving.sync_runs`, `SELECT` and `INSERT` on
  `serving.sync_requests`. Nothing else in `serving`; in particular no right on the copied data tables.
- `agent_ro` keeps `SELECT` on every table in `serving` through the default privileges, except that
  `control.sql` revokes it on `serving.sync_runs` and `serving.sync_requests` (the agent needs only
  `serving.sync_state`, for `data_as_of`).
- `sync_writer` stays the only role that creates, writes, renames and drops objects in `serving` (the worker creates
  the copy and its control tables itself; `app_owner` still cannot touch `serving`).
- Proven by `tests/foundation/test_permissions.py` and `sync/tests/test_control_sql.py`.
