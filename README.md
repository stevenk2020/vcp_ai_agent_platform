# AI Agent Platform

An internal platform where about 50 users ask questions about company data in a chat, answered by an
AI agent that can only read curated copies of the data. The full design is in
[`AI Agent Platform — Review & Technical Spec.md`](AI%20Agent%20Platform%20—%20Review%20%26%20Technical%20Spec.md);
features are built one at a time with [Spec Kit](https://github.com/github/spec-kit) under `specs/`.

This repository currently contains:

- the **foundation layer** (`specs/001-foundation-data-layer`): Postgres (three isolated schemas and a
  read-only agent role), Redis, Caddy for HTTPS, protected secrets, encrypted daily backups with a tested
  restore, and SQL migrations;
- **sign-in** (`specs/002-passwordless-auth`): the `api` service with passwordless sign-in (Google, or an
  emailed code), an admin-managed allowlist, an approval queue, sessions and an audit trail. See
  [`docs/auth.md`](docs/auth.md).
- **web pages** (`specs/003-web-sign-in-admin-ui`): the start page with Google sign-in, the access request page for
  people waiting for approval, and the admin area at `/admin` (approval queue, allowlist, people, audit log). Plain
  static files served by the `api` service; no framework, no build step.
- the **sync worker** (`specs/004-sales-contract-sync`): the `sync` service copies the SQL Server table `dbo.sales_contract`
  into `serving.sales_contract` about every 10 minutes (atomic swap, sanity guards, encrypted read-only connection, "data as
  of"), with a **Data** tab in the admin area. See [`docs/sync.md`](docs/sync.md).

## Prerequisites

Linux with Docker Engine 24+ and Compose v2.29+, `make`, and [`uv`](https://docs.astral.sh/uv/)
(runs the tests). Your shell must be in the `docker` group (`docker ps` works without `sudo`).

## First run

```bash
cp .env.example .env     # optional: adjust the non-secret settings
make secrets             # creates random passwords in secrets/ (ignored by git)
make up                  # starts postgres, redis, bootstrap, migrations
docker compose ps        # every long-running service is healthy
make test                # data-layer acceptance suite (about 90 seconds)
make test-api            # API tests, in an isolated project (never touches the live database)
```

Other commands: `make down`, `make migrate`, `make up-prod` (adds Caddy on
`vcp.onepercentdata.site`), `make backup`, `make restore-test`, `make scan-secrets`, `make test-sync` (sync worker tests, isolated), `make source-secret`, `make sync-status`, `make sync-now`. Run a subset of
the tests with `make test ARGS="-k permissions"` (not `make test -- -k ...`).

## Where things are

| Path | What |
| --- | --- |
| `api/` | the API service (FastAPI): sign-in, allowlist, sessions, audit, and the web pages in `api/app/web`; tests in `api/tests` |
| `compose.yaml`, `compose.backup.yaml` | services; the backup service is loaded only once `secrets/backup_age_public_key` and `secrets/rclone_config` both exist |
| `db/bootstrap/` | idempotent extensions, schemas, roles and grants (run on every `make up`) |
| `db/migrations/` | versioned SQL migrations (dbmate), tracked in `app.schema_migrations` |
| `secrets/` | one file per secret; nothing but `README.md` is tracked |
| `backup/`, `scripts/` | backup image and operator scripts |
| `tests/foundation/` | acceptance tests (roles, timeout, migrations, backup and restore, secrets) |
| `docs/` | [`auth.md`](docs/auth.md), [`vps-setup.md`](docs/vps-setup.md), [`restore.md`](docs/restore.md), [`phase0-verification.md`](docs/phase0-verification.md) |
| `specs/001-foundation-data-layer/`, `specs/002-passwordless-auth/`, `specs/003-web-sign-in-admin-ui/` | spec, plan, tasks, contracts, quickstart |
| `.specify/memory/constitution.md` | the project's non-negotiable principles |

## Rules worth knowing

- The agent connects only as `agent_ro`: read-only, 30-second timeout, `serving` schema only.
- Never commit `.env` or anything in `secrets/` (`make scan-secrets` checks the tree and the history).
- Postgres is bound to `127.0.0.1`; only Caddy publishes public ports.
