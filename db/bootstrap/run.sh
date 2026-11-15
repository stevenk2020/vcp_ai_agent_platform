#!/bin/sh
# Run every bootstrap file in order (10-, 20-, 30-). Idempotent; used by the db-init service.
set -eu

PGPASSWORD="$(cat /run/secrets/postgres_superuser_password)"
export PGPASSWORD

for f in /bootstrap/10-extensions.sql /bootstrap/20-schemas-roles.sh /bootstrap/30-privileges.sql; do
  echo "bootstrap: $f"
  case "$f" in
    *.sql) psql -X -v ON_ERROR_STOP=1 -f "$f" ;;
    *.sh)  sh "$f" ;;
  esac
done
echo "bootstrap: done"
