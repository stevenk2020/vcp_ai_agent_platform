#!/usr/bin/env bash
# Wait until every service of the selected compose project is OK:
#   - services with a healthcheck: Health == healthy
#   - one-shot services (restart "no", no healthcheck): exited with code 0
#   - every long-running service MUST define a healthcheck, otherwise this script waits for it to exit
# `docker compose up --wait` treats an exited one-shot container as a failure, so we poll ourselves.
# Usage: scripts/wait-healthy.sh [compose args such as -f compose.yaml --profile prod]
set -euo pipefail

timeout="${WAIT_TIMEOUT:-300}"
deadline=$((SECONDS + timeout))
compose=(docker compose "$@")

services="$("${compose[@]}" config --services)"
# WAIT_SERVICES (space separated) limits the check to some services, e.g. an isolated test project
services="${WAIT_SERVICES:-$services}"

while true; do
  status="$("${compose[@]}" ps --all --format json | python3 -I -c '
import json, sys
wanted = set(sys.argv[1].split())
rows = {}
for line in sys.stdin:
    line = line.strip()
    if line:
        row = json.loads(line)
        rows[row["Service"]] = row
bad = []
for name in sorted(wanted):
    row = rows.get(name)
    if row is None:
        bad.append(f"{name}: not created")
        continue
    state, health, code = row.get("State"), row.get("Health"), row.get("ExitCode", 0)
    if state == "exited":
        if code != 0:
            bad.append(f"{name}: exited with code {code}")
    elif health:
        if health != "healthy":
            bad.append(f"{name}: {health}")
    else:
        # no healthcheck and not exited: a one-shot that is still running (or a long-running
        # service missing its healthcheck, which this stack does not allow)
        bad.append(f"{name}: {state} (no healthcheck, waiting for exit 0)")
print("; ".join(bad))
' "$services")"

  if [[ -z "$status" ]]; then
    echo "all services ready"
    exit 0
  fi
  # a one-shot that failed will not recover: stop early
  if [[ "$status" == *"exited with code"* ]]; then
    echo "not ready: $status" >&2
    exit 1
  fi
  if ((SECONDS >= deadline)); then
    echo "timeout after ${timeout}s, not ready: $status" >&2
    exit 1
  fi
  sleep 2
done
