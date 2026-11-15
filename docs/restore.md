# Backup and restore

Backups are produced by the `backup` service (see `compose.backup.yaml`):

- every day at `BACKUP_SCHEDULE` (default `0 2 * * *`, container time),
- a `pg_dump` of the `platform` database plus a roles dump (`globals`, no passwords),
- both encrypted with `age` using the **public** key in `secrets/backup_age_public_key`,
- uploaded to `BACKUP_REMOTE` (Google Drive, default `gdrive:platform-backups`),
- file names: `platform-YYYYMMDD-HHMMSS.dump.age` and `platform-YYYYMMDD-HHMMSS.globals.sql.age`,
- kept 7 days on the VPS volume and 30 days on the remote.

`docker compose ps` shows `backup` as `unhealthy` when the last successful backup is older than
26 hours (or none succeeded within 26 hours of the first start).

The age **private** key is not on the VPS. Keep it in your password manager; without it a backup
cannot be read.

## Enable backups (once)

1. Create a key pair on your own machine: `age-keygen -o age_identity.txt`. The line
   `# public key: age1...` is the public key.
2. Put only the `age1...` string into `secrets/backup_age_public_key` on the VPS. Store
   `age_identity.txt` in your password manager and delete the local copy.
3. Create the Google Drive remote (research R-6): on your machine run
   `ssh -L localhost:53682:localhost:53682 <user>@<vps>`, then on the VPS `rclone config`, remote
   name `gdrive`, type `drive`, your own client id/secret, answer *yes* to browser authentication
   and open the printed link on your machine. Copy the resulting `[gdrive]` section into
   `secrets/rclone_config`. Publish your Google OAuth consent screen, otherwise the token expires
   after 7 days.
4. `make up` (it loads `compose.backup.yaml` once both the key and the rclone config exist), then `make backup`, and check the
   two `.age` files appear in the Drive folder.

## Rehearse a restore (safe, touches nothing live)

```bash
AGE_IDENTITY_FILE=/path/to/age_identity.txt make restore-test
```

This restores the latest local backup into a scratch database `restore_test`, compares table lists
and row counts of `app`, `serving` and `kb` with the live database, and drops the scratch database.
Exit code 0 means they match exactly. Do this after enabling backups and then now and then.

## Restore for real (disaster recovery)

A restore only goes into an **empty** database. Restoring into a database that already has data
is refused on purpose: drop and recreate it first (step 4).

1. **Get the backup file.** From the VPS volume: `docker compose exec backup ls /var/lib/backup/dumps`.
   From Drive, on a machine with the `gdrive` remote configured:
   `rclone copy gdrive:platform-backups/platform-YYYYMMDD-HHMMSS.dump.age .` (and the matching
   `.globals.sql.age`). On a brand-new VPS: clone the repo, copy your saved `secrets/` files back
   (keep a copy of them in your password manager), and run `make up` first so the stack, roles,
   schemas and grants exist.
2. **Stop what writes to the database** (later features' services; today nothing else writes).
3. **Decrypt** with the private key (the `backup` image has `age`):
   ```bash
   docker compose -f compose.yaml -f compose.backup.yaml run --rm --no-deps -T \
     --entrypoint age -v "$PWD":/work -v /path/to/age_identity.txt:/identity:ro backup \
     -d -i /identity -o /work/platform.dump /work/platform-YYYYMMDD-HHMMSS.dump.age
   ```
4. **Empty the target database** (this deletes the current data; double check you mean it):
   ```bash
   docker compose exec postgres psql -U postgres -d postgres \
     -c "DROP DATABASE IF EXISTS platform WITH (FORCE)" -c "CREATE DATABASE platform"
   ```
5. **Restore** (superuser, needed for extensions):
   ```bash
   docker compose exec -T postgres pg_restore -U postgres -d platform --exit-on-error < platform.dump
   ```
   The roles already exist because the bootstrap creates them. The `globals` file is for reference
   (role names and memberships); you only need it if the roles were lost, in which case decrypt it
   the same way and run it with `psql -U postgres -d postgres -f`.
6. **Re-apply bootstrap and migrations:** `make up` (this also re-syncs role passwords and grants).
7. **Verify:** `make test` (or at least `make test ARGS="-k permissions"`), then delete the
   decrypted `platform.dump` from disk.

## Things that can go wrong

- **Disk almost full:** `backup.sh` refuses to run with less than 1 GB free on the backup volume and
  exits non-zero; `last_success` is not updated, so the service turns `unhealthy` after 26 hours.
- **Google token expired / remote unreachable:** the upload fails, `last_success` stays unchanged,
  the service turns `unhealthy`. Re-run `rclone config` (step 3 above) and `make backup`.
- **Lost the age private key:** existing backups cannot be decrypted. Create a new key pair now and
  replace the public key; new backups use it.

## Last tested

Real restore from Google Drive: **not yet** (fill in date, backup file, and result after the first
run of `make restore-test` against a Drive-uploaded backup).

Automated: `tests/foundation/test_backup_restore.py` runs a full backup, upload to a local
"remote", decrypt, restore and comparison on every `make test`.
