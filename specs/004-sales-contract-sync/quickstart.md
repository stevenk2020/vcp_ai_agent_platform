# Quickstart: validating the sales contract sync

## Prerequisites

- The stack is running (`make up` or `make up-prod`), feature 003 deployed.
- `secrets/source_sqlserver` filled once with `make source-secret` (copies the `sql_server_*` values from `.env`).
- You can sign in to the admin area as an admin.

## Automated checks (no real SQL Server)

```bash
make test-sync       # worker: copy, swap, guards, retries, secrets, read-only, encryption setting, definitions
make test-api        # includes the admin sync endpoints and the deny-by-default walk
make test            # foundation: grants, swap with a concurrent reader, backup includes the copy
make scan-secrets
```

All green, and the counts of each suite are reported.

## Manual walk-through against the real source (operator)

1. `make up-prod`. In `make sync-status` the table shows `no_data` or the first run `running`; the log of the `sync`
   service shows JSON lines and no secret.
2. Wait for the first run (up to about 2 minutes while the source job holds its commit, SC-002 allows 5 minutes).
   `make sync-status`: state `healthy`, source rows equal to copied rows (about 35k), `data_as_of` recent.
3. Compare with the source: `SELECT count(*)` on both sides (`docker compose exec postgres psql ...` and your SQL
   Server tool) are equal; spot-check three rows by `(entity, row_id)`: text with diacritics, a unit price with
   decimals, a null date.
4. Confirm the excluded columns are absent: `\d serving.sales_contract` has 62 columns and no `employee_*`.
5. Open the admin page, tab **Data**: status line healthy with "data as of", last runs listed. Press **Sync now**,
   confirm: a new run appears within about 10 seconds and finishes; pressing again while it runs shows the busy message.
6. While a run is waiting on the source, query the copy from another session: it answers immediately with the old
   complete table (never empty).
7. Guards cannot be triggered safely on the real source, so they are proven by the automated tests: confirm
   `make test-sync ARGS="-k guards"` passes. Also confirm the connection is encrypted by configuration: the log at start
   says `source encryption: require` and no warning about `SOURCE_ENCRYPT`.
8. Source-down drill: stop the network to the source for two ticks (or temporarily rename the secret host to a bad
   value, restart `sync`): runs `failed` with `source_unreachable`, state `failing` after 3, the copy still
   answers, `data_as_of` ages to `stale`; restore and the state returns to `healthy` by itself.
9. Read-only proof: the log at start says the source account was checked (not sysadmin, not db_owner, no write
   permission). If you ever point the secret at a privileged account the worker refuses to run.
10. When the DBA turns snapshot isolation on: set `SOURCE_SNAPSHOT_READ=on`, `make up-prod`; the log says "snapshot
    read"; runs no longer wait for the source job (duration drops to seconds). No code change.
11. Run `make backup` and `make restore-test` (if backup is enabled): the restore contains `serving.sales_contract`
    and `sync_runs`; a following sync repairs the copy.

Report any failing step with the exact text shown.
