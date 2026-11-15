#!/bin/sh
# Create or update roles and schemas. Idempotent; runs as the Postgres superuser on every start.
# Passwords are read from /run/secrets; they are never echoed and never written to SQL files.
set -eu

: "${PGHOST:?}" "${PGUSER:?}" "${PGDATABASE:?}"
PGPASSWORD="$(cat /run/secrets/postgres_superuser_password)"
export PGPASSWORD

# Passwords reach psql through environment variables (\getenv below), not through -v on the command
# line, so they never appear in the process arguments.
APP_OWNER_PW="$(cat /run/secrets/app_owner_password)"
SYNC_WRITER_PW="$(cat /run/secrets/sync_writer_password)"
AGENT_RO_PW="$(cat /run/secrets/agent_ro_password)"
BACKUP_RO_PW="$(cat /run/secrets/backup_ro_password)"
export APP_OWNER_PW SYNC_WRITER_PW AGENT_RO_PW BACKUP_RO_PW

psql -X -v ON_ERROR_STOP=1 <<'SQL'
\getenv app_owner_pw APP_OWNER_PW
\getenv sync_writer_pw SYNC_WRITER_PW
\getenv agent_ro_pw AGENT_RO_PW
\getenv backup_ro_pw BACKUP_RO_PW

-- Roles: LOGIN only, never SUPERUSER / CREATEDB / CREATEROLE.
SELECT format(
  'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE',
  r.name)
FROM (VALUES ('app_owner'), ('sync_writer'), ('agent_ro'), ('backup_ro')) AS r(name)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r.name)
\gexec

ALTER ROLE app_owner   PASSWORD :'app_owner_pw'   LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER ROLE sync_writer PASSWORD :'sync_writer_pw' LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER ROLE agent_ro    PASSWORD :'agent_ro_pw'    LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER ROLE backup_ro   PASSWORD :'backup_ro_pw'   LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;

-- Schemas and ownership (data-model.md).
CREATE SCHEMA IF NOT EXISTS app     AUTHORIZATION app_owner;
CREATE SCHEMA IF NOT EXISTS kb      AUTHORIZATION app_owner;
CREATE SCHEMA IF NOT EXISTS serving AUTHORIZATION sync_writer;
ALTER SCHEMA app     OWNER TO app_owner;
ALTER SCHEMA kb      OWNER TO app_owner;
ALTER SCHEMA serving OWNER TO sync_writer;

-- Backup role may read everything (for pg_dump) and nothing else.
GRANT pg_read_all_data TO backup_ro;
SQL

echo "20-schemas-roles: roles and schemas are in place"
