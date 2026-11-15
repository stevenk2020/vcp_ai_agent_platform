#!/usr/bin/env bash
# Run the sync worker tests in an isolated Compose project so the live database is never touched
# (this host is also production). Usage: make test-sync ARGS="-k guards"
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

PROJECT=platform-synctest
if [[ "$PROJECT" == "platform" ]]; then
  echo "test-sync: refusing to run against the live project" >&2
  exit 2
fi

# Postgres of the test project must not clash with the live one or with the API test project
export POSTGRES_PORT=55434
export BUILDKIT_PROGRESS=quiet
args=(-p "$PROJECT" --profile test)

cleanup() {
  docker compose "${args[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if ! docker compose "${args[@]}" config --services | grep -qx sync-test; then
  echo "test-sync: the sync-test service is not defined in compose.yaml yet" >&2
  exit 1
fi

docker compose "${args[@]}" build --quiet sync-test >/dev/null
docker compose "${args[@]}" up -d postgres db-init migrate
WAIT_SERVICES="postgres db-init migrate" bash scripts/wait-healthy.sh "${args[@]}"

# shellcheck disable=SC2086
# ARGS may contain quoted words (e.g. -k 'a or b'); let the shell interpret the quotes once
eval "set -- ${ARGS:-}"
docker compose "${args[@]}" run --rm -e API_TEST_ISOLATED=1 sync-test pytest "$@"
