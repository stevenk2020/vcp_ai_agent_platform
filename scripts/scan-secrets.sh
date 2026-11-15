#!/usr/bin/env bash
# Fail if any secret value appears in tracked files or anywhere in git history.
# Values come from secrets/* (except README.md; rclone_config only for secret-looking keys) and from
# secret-looking keys in .env
# (names containing password, secret, token or key). Only values of 8+ characters count.
# Output lists file paths and commit hashes, never the matched value.
# SCAN_ROOT overrides the repository to scan (used by the tests).
set -euo pipefail

cd "${SCAN_ROOT:-$(dirname "${BASH_SOURCE[0]}")/..}"

values() {
  local f
  for f in secrets/*; do
    [[ -f "$f" && "$(basename "$f")" != "README.md" ]] || continue
    if [[ "$(basename "$f")" == "rclone_config" || "$(basename "$f")" == "api_external" ]]; then
      # key=value file (rclone_config, api_external): only the values of secret-looking keys (client_secret, token, ...) count;
      # section headers such as [gdrive] and keys like type or scope are not secrets
      tr -d '\r' < "$f" | awk -F' *= *' 'tolower($1) ~ /(password|secret|token|key)/ { v=$0; sub(/^[^=]*= */, "", v); if (length(v) >= 8) print v }'
    else
      tr -d '\r' < "$f" | awk 'length($0) >= 8'
    fi
  done
  if [[ -f .env ]]; then
    # KEY=VALUE lines whose key looks secret; strip optional quotes
    awk -F= '/^[[:space:]]*#/ { next } !/=/ { next } tolower($1) ~ /(password|secret|token|key)/ { v=$0; sub(/^[^=]*=/, "", v); gsub(/^["'\'']|["'\'']$/, "", v); if (length(v) >= 8) print v }' .env
  fi
}

count="$(values | wc -l | tr -d ' ')"
if [[ "$count" == "0" ]]; then
  echo "scan-secrets: no secret values found locally, nothing to scan for"
  exit 0
fi

git rev-parse --git-dir >/dev/null 2>&1 || { echo "scan-secrets: not a git repository" >&2; exit 2; }

status=0

# 1. tracked files in the working tree
if hits="$(git grep -I -l -F -f <(values) 2>/dev/null)"; [[ -n "$hits" ]]; then
  echo "scan-secrets: secret value found in tracked files:" >&2
  echo "$hits" | sed 's/^/  /' >&2
  status=1
fi

# 2. every commit in history (hash:path only)
if git rev-parse --verify -q HEAD >/dev/null; then
  # shellcheck disable=SC2046
  if hits="$(git grep -I -l -F -f <(values) $(git rev-list --all) -- 2>/dev/null)"; [[ -n "$hits" ]]; then
    echo "scan-secrets: secret value found in git history (commit:path):" >&2
    echo "$hits" | sed 's/^/  /' >&2
    status=1
  fi
fi

if ((status == 0)); then
  echo "scan-secrets: clean ($count secret values checked against tracked files and history)"
fi
exit "$status"
