# Contract: Operator Commands

The interface the developer/operator uses. Every command exits non-zero on failure.

| Command | Does | Success signal |
| --- | --- | --- |
| `make secrets` | Creates any missing file in `secrets/` with a random value; never overwrites an existing one; prompts-free | Lists created file names, no values |
| `make up` | Starts postgres, redis, bootstrap, migrations, backup job and waits for health (development) | Exit 0 and every service `healthy` |
| `make up-prod` | Same plus Caddy with HTTPS for `PLATFORM_DOMAIN` | Exit 0, `https://$PLATFORM_DOMAIN/healthz` returns 200 |
| `make down` | Stops the stack, keeps volumes | Exit 0 |
| `make migrate` | Applies pending migrations only | Exit 0; second run changes nothing |
| `make backup` | Runs one backup now (same code path as the daily job) | New file locally and off-site; `last_success` updated |
| `make restore-test` | Restores the latest backup into a scratch database and compares per-schema row counts | Exit 0 and a matching-counts report |
| `make test` | Runs the acceptance suite in `tests/foundation` | All tests pass |
| `make scan-secrets` | Fails if any secret value appears in tracked files or history | Exit 0 |

## Health

| Service | Healthy when |
| --- | --- |
| `postgres` | `pg_isready` succeeds for database `platform` |
| `redis` | `PING` returns `PONG` |
| `db-init`, `migrate` | One-shot services; exit code 0 |
| `backup` | Last successful backup is less than 26 hours old |
| `caddy` (prod) | `/healthz` returns 200 |

`docker compose ps` is the single place the operator checks; `unhealthy` on `backup` means the last
backup is missing or stale (FR-013).

## Restore procedure (summary, full text in `docs/restore.md`)

1. Stop dependent services; keep `postgres` running.
2. Fetch the chosen backup and decrypt with the operator's age private key.
3. Restore the globals file, then `pg_restore` into an empty `platform` database.
4. Run `make up` to re-apply bootstrap (re-syncs role passwords and grants) and migrations.
5. Run `make test` or at least the permission matrix.

## Environment variables (non-secret, in `.env.example`)

| Name | Meaning | Default |
| --- | --- | --- |
| `PLATFORM_DOMAIN` | Domain for HTTPS in production | `vcp.onepercentdata.site` |
| `POSTGRES_DB` | Database name | `platform` |
| `BACKUP_SCHEDULE` | Cron expression for the daily job | `0 2 * * *` |
| `BACKUP_RETENTION_LOCAL_DAYS` | Local retention | `7` |
| `BACKUP_REMOTE` | rclone remote path on Google Drive | `gdrive:platform-backups` |
| `BACKUP_RETENTION_REMOTE_DAYS` | Retention on Google Drive | `30` |
