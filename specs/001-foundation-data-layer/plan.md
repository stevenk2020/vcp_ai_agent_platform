# Implementation Plan: Foundation Environment and Data Layer

**Branch**: `001-foundation-data-layer` (working on `main`; no feature branch created) | **Date**: 2026-11-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-foundation-data-layer/spec.md`

## Summary

Deliver the base infrastructure every later feature runs on: a Docker Compose stack (Postgres 16 with
pgvector, Redis, Caddy for HTTPS on the VPS) started with one command, a Postgres layout with three
isolated schemas (`app`, `serving`, `kb`) and three least-privilege roles (`app_owner`, `sync_writer`,
`agent_ro`), file-based secrets, a daily encrypted off-site backup with a tested restore, and a
versioned SQL migration mechanism. An idempotent bootstrap step (run on every start) owns everything
that needs a superuser or a secret (extensions, schemas, roles, grants); migrations own tables.
Acceptance is verified by an automated test suite that connects as each role and tries the permitted
and forbidden operations. See [research.md](research.md) for the decisions behind each choice.

## Technical Context

**Language/Version**: No application code. Docker Compose v2 manifests, SQL, POSIX shell (bootstrap, backup, scripts); Python 3.12 + pytest + psycopg 3 for the acceptance tests only

**Primary Dependencies**: `pgvector/pgvector:pg16` image (Postgres 16; `pg_trgm` and `citext` ship as contrib), `redis:7-alpine`, `caddy:2`, `dbmate` (SQL migrations, run as a one-shot container), `rclone` and `age` inside a small backup image

**Storage**: PostgreSQL 16 (schemas `app`, `serving`, `kb`), Redis 7 (cache, no persistence required), named Docker volumes, off-site object/remote storage for backups (destination chosen by the operator, accessed via rclone)

**Testing**: pytest + psycopg against the running stack (role permission matrix, 30 s timeout, migrations idempotency, backup/restore round trip, secrets scan with a small script)

**Target Platform**: Linux (Ubuntu VPS in production; Linux/WSL developer machine), Docker Engine 24+ with Compose v2

**Project Type**: Infrastructure / ops repository (no web or mobile code in this feature)

**Performance Goals**: Stack healthy from a clean machine in under 10 minutes including image pulls (SC-001); backup of the expected data size (a few hundred MB at most at this scale) completes in under 5 minutes

**Constraints**: Single VPS (~4 vCPU / 8 GB, unmeasured); `agent_ro` queries cancelled at 30 s; no secret values in git or in containers that do not need them; Postgres and Redis not published on public interfaces; no `docker.sock` mounts (constitution III)

**Scale/Scope**: ~50 users, source tables around 100k rows (spec §1), one topic (`dbo.sales_contract`) to be served later

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design (below).*

| Principle | Applies here? | Status |
| --- | --- | --- |
| I. Permission-First Security | Partly: DB roles are the lowest enforcement layer; auth itself is out of scope | Pass. Roles are least-privilege and enforced by the database, not by prompts |
| II. Data Isolation and Safe SQL | Yes: `serving` + `agent_ro` (read-only, 30 s timeout, only `serving`) | Pass. Delivered by FR-004, FR-006, FR-007. The sqlglot validator and atomic swap are later features, but the role/privilege model here is designed for them (`sync_writer` owns `serving`, default privileges grant `agent_ro` SELECT on swapped-in tables) |
| III. Untrusted Content and Contained Execution | Partly: no `docker.sock` mount, internal-only network | Pass. Compose mounts no Docker socket; Postgres and Redis publish no host ports in production |
| IV. Small Core, Pluggable Everything | Yes: avoid unnecessary services | Pass. Five small services; no orchestrator, no extra tooling beyond Compose |
| V. Verifiable Answers | No (no answers produced) | N/A |
| VI. Observability and Auditability | Partly: stale-backup visibility, health status | Pass. Backup freshness is a health check; usage/audit logging is later |
| Technology and Operational Constraints | Yes | Pass. Stack matches the constitution (Postgres 16 + pgvector + pg_trgm + citext, Redis, Docker Compose, Caddy, daily off-machine backup). Secrets live in gitignored files |

No violations, so Complexity Tracking is empty.

**Post-design re-check**: Pass. The design in [data-model.md](data-model.md) and [contracts/](contracts/) keeps the same
properties; one residual risk is recorded in research (R-4): a role can override its own
`statement_timeout` with `SET` unless the SQL validator (later feature) forbids `SET`. The role
defaults satisfy the acceptance test; the validator closes the gap.

## Project Structure

### Documentation (this feature)

```text
specs/001-foundation-data-layer/
├── plan.md              # This file
├── research.md          # Phase 0: decisions and alternatives
├── data-model.md        # Phase 1: schemas, roles, privileges, state
├── quickstart.md        # Phase 1: validation scenarios
├── contracts/
│   ├── database-access.md    # Role/privilege matrix and connection contract
│   └── operator-commands.md  # make targets, health, backup/restore interface
└── tasks.md             # Phase 2 (/speckit-tasks, not created here)
```

### Source Code (repository root)

```text
compose.yaml                 # postgres, redis, db-init, migrate, backup, caddy (prod profile)
Makefile                     # up, up-prod, down, migrate, backup, restore-test, test
.env.example                 # non-secret settings and secret names (no values)
secrets/                     # gitignored; one file per secret; README.md only is tracked
db/
├── bootstrap/               # idempotent, runs as superuser on every start
│   ├── 10-extensions.sql
│   ├── 20-schemas-roles.sh  # reads passwords from /run/secrets, applies roles and grants
│   └── 30-privileges.sql
└── migrations/              # dbmate SQL migrations (app/kb tables), one baseline migration
caddy/
└── Caddyfile                # HTTPS entry point, /healthz placeholder
backup/
├── Dockerfile               # postgres client + rclone + age
├── backup.sh                # dump, encrypt, upload, record last success
└── healthcheck.sh           # fails if last success is older than 26 hours
scripts/
├── gen-secrets.sh           # creates random secret files that do not yet exist
├── restore-test.sh          # restore latest backup into scratch DB and compare counts
└── scan-secrets.sh          # fails if known secret values appear in tracked files
tests/
└── foundation/              # pytest: roles, timeout, migrations, backup round trip
docs/
├── restore.md               # documented restore procedure
└── phase0-verification.md   # the four open sales_contract items with owner/status/findings
```

**Structure Decision**: Single infrastructure repository at the repository root. Later features (API,
sync worker, UI) add their own top-level directories and Compose services; this feature owns only
the shared data layer, which is why migrations and bootstrap live under `db/` and are not tied to any
application package.

## Complexity Tracking

No constitution violations to justify.
