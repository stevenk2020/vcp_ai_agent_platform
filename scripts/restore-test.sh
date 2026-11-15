#!/usr/bin/env bash
# Restore the latest local backup into a scratch database and compare it with the live database.
# Usage: AGE_IDENTITY_FILE=/path/to/age-private-key make restore-test
# COMPOSE_ARGS can select other compose files or a project (used by the tests).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

: "${AGE_IDENTITY_FILE:?set AGE_IDENTITY_FILE to the path of your age private key}"
[[ -s "$AGE_IDENTITY_FILE" ]] || { echo "restore-test: $AGE_IDENTITY_FILE is missing or empty" >&2; exit 1; }

# shellcheck disable=SC2206
compose_args=(${COMPOSE_ARGS:--f compose.yaml -f compose.backup.yaml})

# The superuser secret is mounted only into this one-off container, never into the backup service.
exec docker compose "${compose_args[@]}" run --rm --no-deps -T \
  --entrypoint /usr/local/bin/restore.sh \
  -v "$(realpath "$AGE_IDENTITY_FILE")":/identity:ro \
  -v "$PWD/secrets/postgres_superuser_password":/run/secrets/postgres_superuser_password:ro \
  backup
