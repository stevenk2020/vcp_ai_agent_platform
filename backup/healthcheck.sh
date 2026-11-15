#!/bin/bash
# Healthy only if the last successful backup is younger than 26 hours.
# A freshly started service with no backup yet gets a 26-hour grace period from its first start.
set -u

STATE=/var/lib/backup
limit=$((26 * 3600))
now="$(date +%s)"

if [[ -s "$STATE/last_success" ]]; then
  last="$(cat "$STATE/last_success")"
  if (( now - last < limit )); then
    exit 0
  fi
  echo "last successful backup is older than 26 hours" >&2
  exit 1
fi

started="$(cat "$STATE/started_at" 2>/dev/null || echo "$now")"
if (( now - started < limit )); then
  exit 0
fi
echo "no successful backup within 26 hours of the first start" >&2
exit 1
