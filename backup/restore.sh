#!/bin/bash
# Restore the latest local backup into a scratch database and compare it with the live database.
# Needs, mounted by scripts/restore-test.sh:
#   /identity                                      the operator's age private key
#   /run/secrets/postgres_superuser_password       superuser password (restore needs superuser)
# Exit 0 only if table lists and row counts of app, serving and kb match exactly.
set -euo pipefail

: "${POSTGRES_DB:=platform}"
SCRATCH=restore_test
DUMPS=/var/lib/backup/dumps

latest="$(ls -1 "$DUMPS"/platform-*.dump.age 2>/dev/null | sort | tail -n1 || true)"
if [[ -z "$latest" ]]; then
  echo "restore-test: no backup found in $DUMPS (run make backup first)" >&2
  exit 1
fi
echo "restore-test: using $(basename "$latest")"

export PGHOST=postgres PGUSER=postgres
PGPASSWORD="$(cat /run/secrets/postgres_superuser_password)"
export PGPASSWORD

work="$(mktemp -d)"
trap 'rm -rf "$work"; psql -X -q -d postgres -c "DROP DATABASE IF EXISTS ${SCRATCH}" >/dev/null 2>&1 || true' EXIT

age -d -i /identity -o "$work/db.dump" "$latest"

psql -X -q -v ON_ERROR_STOP=1 -d postgres \
  -c "DROP DATABASE IF EXISTS ${SCRATCH}" -c "CREATE DATABASE ${SCRATCH}"
pg_restore --dbname="$SCRATCH" --exit-on-error "$work/db.dump"

# per-table row counts for the three data areas, as "schema.table|count"
counts_sql="
SELECT table_schema || '.' || table_name || '|' ||
       (xpath('/row/c/text()',
              query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name),
                           false, true, '')))[1]::text
FROM information_schema.tables
WHERE table_schema IN ('app', 'serving', 'kb') AND table_type = 'BASE TABLE'
ORDER BY 1"

psql -X -At -d "$POSTGRES_DB" -c "$counts_sql" > "$work/live.txt"
psql -X -At -d "$SCRATCH" -c "$counts_sql" > "$work/restored.txt"

if diff -u "$work/live.txt" "$work/restored.txt"; then
  echo "restore-test: OK, $(wc -l < "$work/live.txt") tables match (structure and row counts)"
else
  echo "restore-test: MISMATCH between live database and restored backup" >&2
  exit 1
fi
