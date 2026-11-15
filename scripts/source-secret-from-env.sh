#!/usr/bin/env bash
# Copy the five SQL Server settings from .env into secrets/source_sqlserver (mode 600).
# - prints only the KEY names that were copied, never a value
# - refuses to overwrite a non-empty secrets/source_sqlserver unless --force is given
# - SOURCE_ENV_FILE and SOURCE_SECRET_FILE override the paths (used by the tests)
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
env_file="${SOURCE_ENV_FILE:-.env}"
secret_file="${SOURCE_SECRET_FILE:-secrets/source_sqlserver}"
force=0
[[ "${1:-}" == "--force" ]] && force=1

if [[ ! -f "$env_file" ]]; then
  echo "source-secret: $env_file not found" >&2
  exit 1
fi
if [[ -s "$secret_file" && $force -eq 0 ]]; then
  echo "source-secret: $secret_file already has content; use --force to overwrite" >&2
  exit 1
fi

umask 077
mkdir -p "$(dirname "$secret_file")"
tmp="$(mktemp "${secret_file}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

copied=()
for key in host port user password database; do
  # lowercase keys in .env; first match wins; quotes and trailing spaces stripped
  value="$(grep -E "^sql_server_${key}=" "$env_file" | head -n1 | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e "s/^[\"']//" -e "s/[\"']\$//")" || true
  if [[ -z "$value" ]]; then
    echo "source-secret: sql_server_${key} is missing or empty in $env_file" >&2
    exit 1
  fi
  printf 'SQL_SERVER_%s=%s\n' "${key^^}" "$value" >> "$tmp"
  copied+=("SQL_SERVER_${key^^}")
done

chmod 600 "$tmp"
mv "$tmp" "$secret_file"
trap - EXIT
echo "copied: ${copied[*]}"
echo "the worker reads $secret_file; you can now delete the sql_server_* lines from $env_file"
