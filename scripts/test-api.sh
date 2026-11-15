#!/usr/bin/env bash
# Run the API integration tests in an isolated Compose project so the live database and Redis are
# never touched (this host is also production). Usage: make test-api ARGS="-k signin"
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

PROJECT=platform-apitest
if [[ "$PROJECT" == "platform" ]]; then
  echo "test-api: refusing to run against the live project" >&2
  exit 2
fi

# Postgres of the test project must not clash with the live one on 127.0.0.1:5432
export POSTGRES_PORT=55433
export BUILDKIT_PROGRESS=quiet   # keep the output about the tests, not the image build
args=(-p "$PROJECT" --profile test)

cleanup() {
  docker compose "${args[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

if ! docker compose "${args[@]}" config --services | grep -qx api-test; then
  echo "test-api: the api-test service is not defined in compose.yaml yet" >&2
  exit 1
fi

docker compose "${args[@]}" build --quiet api-test >/dev/null
docker compose "${args[@]}" up -d postgres redis db-init migrate
WAIT_SERVICES="postgres redis db-init migrate" bash scripts/wait-healthy.sh "${args[@]}"

# shellcheck disable=SC2086
docker compose "${args[@]}" run --rm -e API_TEST_ISOLATED=1 api-test pytest ${ARGS:-}
