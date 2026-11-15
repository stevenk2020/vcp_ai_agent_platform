#!/usr/bin/env bash
# Create any missing password secret in secrets/ with a random value.
# - never overwrites an existing file
# - never prints a secret value, only file names
# - does NOT create backup_age_public_key or rclone_config (operator-provided)
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
mkdir -p secrets
umask 077

names=(
  postgres_superuser_password
  app_owner_password
  sync_writer_password
  agent_ro_password
  backup_ro_password
)

created=()
for name in "${names[@]}"; do
  file="secrets/${name}"
  if [[ -s "$file" ]]; then
    continue
  fi
  # 32 random bytes, base64 without newline; strip characters that complicate URLs and shells
  openssl rand -base64 48 | tr -d '\n/+=' | cut -c1-43 | tr -d '\n' > "$file"
  chmod 600 "$file"
  created+=("$name")
done

if ((${#created[@]})); then
  echo "created: ${created[*]}"
else
  echo "all password secrets already exist"
fi

for operator_file in backup_age_public_key rclone_config; do
  if [[ ! -s "secrets/${operator_file}" ]]; then
    echo "note: secrets/${operator_file} not provided yet (operator-provided; backup stays disabled)"
  fi
done
