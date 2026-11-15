#!/bin/bash
# Render the cron schedule and run crond in the foreground.
set -euo pipefail

if [[ ! -s /run/secrets/backup_age_public_key ]]; then
  echo "backup: /run/secrets/backup_age_public_key is missing or empty; refusing to start" >&2
  exit 1
fi

: "${BACKUP_REMOTE:?BACKUP_REMOTE is required}"
BACKUP_SCHEDULE="${BACKUP_SCHEDULE:-0 2 * * *}"

STATE=/var/lib/backup
mkdir -p "$STATE/dumps"
# Grace period for the healthcheck is measured from the first start (kept across restarts so
# repeated restarts cannot hide a backup that never succeeds).
[[ -f "$STATE/started_at" ]] || date +%s > "$STATE/started_at"

# crond does not pass the container environment on, so write the non-secret settings to a file.
# Values already present in the environment win (used by `docker compose exec -e`).
cat > /etc/backup.env <<EOF
: "\${BACKUP_REMOTE:=${BACKUP_REMOTE}}"
: "\${BACKUP_RETENTION_LOCAL_DAYS:=${BACKUP_RETENTION_LOCAL_DAYS:-7}}"
: "\${BACKUP_RETENTION_REMOTE_DAYS:=${BACKUP_RETENTION_REMOTE_DAYS:-30}}"
: "\${POSTGRES_DB:=${POSTGRES_DB:-platform}}"
export BACKUP_REMOTE BACKUP_RETENTION_LOCAL_DAYS BACKUP_RETENTION_REMOTE_DAYS POSTGRES_DB
EOF

echo "${BACKUP_SCHEDULE} /usr/local/bin/backup.sh >/proc/1/fd/1 2>/proc/1/fd/2" > /etc/crontabs/root
echo "backup: schedule '${BACKUP_SCHEDULE}', remote '${BACKUP_REMOTE}'"
exec crond -f -l 8
