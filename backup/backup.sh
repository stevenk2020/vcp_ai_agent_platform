#!/bin/bash
# One backup run: dump, encrypt with age, upload with rclone, record success, apply retention.
# Exits non-zero on any failure; last_success is only updated after the upload succeeded.
set -euo pipefail

[[ -f /etc/backup.env ]] && . /etc/backup.env
: "${BACKUP_REMOTE:?}" "${BACKUP_RETENTION_LOCAL_DAYS:=7}" "${BACKUP_RETENTION_REMOTE_DAYS:=30}" "${POSTGRES_DB:=platform}"

STATE=/var/lib/backup
DUMPS="$STATE/dumps"
mkdir -p "$DUMPS"

free_kb="$(df -Pk "$STATE" | awk 'NR==2 {print $4}')"
if (( free_kb < 1048576 )); then
  echo "backup: less than 1 GB free on the backup volume (${free_kb} KB), aborting" >&2
  exit 1
fi

export PGHOST=postgres PGUSER=backup_ro PGDATABASE="$POSTGRES_DB"
PGPASSWORD="$(cat /run/secrets/backup_ro_password)"
export PGPASSWORD

stamp="$(date -u +%Y%m%d-%H%M%S)"
dump="$DUMPS/platform-${stamp}.dump.age"
globals="$DUMPS/platform-${stamp}.globals.sql.age"
trap 'rm -f "$dump.partial" "$globals.partial"' EXIT

echo "backup: dumping $POSTGRES_DB"
pg_dump --format=custom | age -R /run/secrets/backup_age_public_key > "$dump.partial"
# roles without passwords (required for a non-superuser); passwords are re-synced by the bootstrap
pg_dumpall --globals-only --no-role-passwords --database="$POSTGRES_DB" \
  | age -R /run/secrets/backup_age_public_key > "$globals.partial"
mv "$dump.partial" "$dump"
mv "$globals.partial" "$globals"

echo "backup: uploading to $BACKUP_REMOTE"
rclone copy --config /run/secrets/rclone_config "$dump" "$BACKUP_REMOTE"
rclone copy --config /run/secrets/rclone_config "$globals" "$BACKUP_REMOTE"

date +%s > "$STATE/last_success.tmp"
mv -f "$STATE/last_success.tmp" "$STATE/last_success"
echo "backup: done $stamp"

# retention is best effort: a failure here must not mark the backup itself as failed
find "$DUMPS" -type f -name 'platform-*.age' -mtime "+${BACKUP_RETENTION_LOCAL_DAYS}" -delete || \
  echo "backup: warning, local retention failed" >&2
rclone delete --config /run/secrets/rclone_config "$BACKUP_REMOTE" \
  --include 'platform-*.age' --min-age "${BACKUP_RETENTION_REMOTE_DAYS}d" || \
  echo "backup: warning, remote retention failed" >&2
