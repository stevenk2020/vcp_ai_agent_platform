# Quickstart: Validate the Foundation Layer

End-to-end checks that prove each success criterion. Interfaces are in
[contracts/](contracts/), structure in [data-model.md](data-model.md).

## Prerequisites

- Linux machine (or the VPS) with Docker Engine 24+ and Compose v2, `make`, and `uv`
- A Google Drive rclone remote named `gdrive` (token created with `rclone authorize "drive"` on a
  machine with a browser or an SSH port forward, stored in `secrets/rclone_config`; see research R-6)
- An age key pair (public key goes in `secrets/backup_age_public_key`, private key stays with you).
  Not created yet: Scenario 4 is blocked until it exists
- For production: `PLATFORM_DOMAIN=vcp.onepercentdata.site` with an ``A` record pointing at the VPS and
  ports 80/443 open

## Scenario 1 — One command to healthy (SC-001, FR-001 to FR-003)

```bash
cp .env.example .env     # then fill non-secret settings
make secrets
time make up
docker compose ps        # every service healthy
make down && make up     # data still there
```

Expect: exit 0, all services `healthy`, total time under 10 minutes on a clean machine.

## Scenario 2 — Agent role is locked down (SC-002, SC-003, FR-004 to FR-007)

```bash
make test -- -k permissions
```

Expect: reads on a seeded `serving` table succeed; every write, DDL, temp-table, and `app`/`kb`
access attempt by `agent_ro` is rejected; a `pg_sleep(31)` is cancelled at 30 s; extensions
`vector`, `pg_trgm`, `citext` are present.

## Scenario 3 — Secrets stay private (SC-005, FR-008 to FR-010)

```bash
make scan-secrets
docker compose config | grep -i -E 'password|secret' # names only, no values
docker inspect $(docker compose ps -q redis) | grep -i password || echo "no secrets in redis"
```

Expect: scan exits 0; no credential values in tracked files; `redis` and `caddy` receive no database
secrets.

## Scenario 4 — Backup and restore (SC-004, FR-011 to FR-013)

```bash
make backup
make restore-test
make test -- -k backup_staleness   # simulates a last_success older than 26 hours
docker compose ps                  # backup shows unhealthy when stale
```

Expect: a new encrypted dump locally and at the remote; restore into a scratch database reproduces
`app`, `serving`, `kb` with matching row counts; a stale job shows `unhealthy`.

## Scenario 5 — Migrations are re-runnable (SC-006, FR-014)

```bash
make migrate
make migrate
```

Expect: both runs exit 0; the second reports nothing to apply; `app.platform_meta` has exactly one
`schema_generation` row.

## Scenario 6 — Verification items are tracked (SC-007, FR-015)

Open `docs/phase0-verification.md`. Expect rows V-1 to V-4, each with owner, status, and a finding
column. The sync worker feature must not start until all four are `resolved`.

## Scenario 7 — HTTPS on the VPS (FR-001, production only)

```bash
make up-prod
curl -fsS https://$PLATFORM_DOMAIN/healthz
```

Expect: HTTP 200 over a valid certificate. If DNS is not ready yet, this scenario is deferred and
the rest still pass (spec Assumptions).
