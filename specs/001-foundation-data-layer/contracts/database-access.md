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

| Operation | `agent_ro` | `sync_writer` | `app_owner` | `backup_ro` |
| --- | --- | --- | --- | --- |
| SELECT in `serving` | ✔ | ✔ | ✘ | ✔ |
| INSERT / UPDATE / DELETE / TRUNCATE in `serving` | ✘ | ✔ | ✘ | ✘ |
| CREATE / ALTER / DROP / RENAME table in `serving` | ✘ | ✔ | ✘ | ✘ |
| SELECT in `app` or `kb` | ✘ | ✘ | ✔ | ✔ |
| Any write or DDL in `app` or `kb` | ✘ | ✘ | ✔ | ✘ |
| CREATE anything in `public` | ✘ | ✘ | ✘ | ✘ |
| CREATE TEMP table | ✘ | ✘ | ✘ | ✘ |
| CREATE ROLE / CREATE EXTENSION | ✘ | ✘ | ✘ | ✘ |

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
