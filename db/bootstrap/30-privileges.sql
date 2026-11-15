-- Privileges and agent_ro hardening (contracts/database-access.md, research R-4).
-- Idempotent; runs as the Postgres superuser on every start.

-- Database-level privileges: resolve the name instead of hard-coding it (POSTGRES_DB is configurable).
DO $$
DECLARE
  db text := current_database();
BEGIN
  EXECUTE format('REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC', db);
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO app_owner, sync_writer, agent_ro, backup_ro', db);
END
$$;

-- Nothing may be created in public, and PUBLIC gets nothing there.
REVOKE ALL ON SCHEMA public FROM PUBLIC;

-- agent_ro: read-only on serving, nothing elsewhere.
REVOKE ALL ON SCHEMA app, kb FROM agent_ro;
REVOKE ALL ON ALL TABLES IN SCHEMA app, kb FROM agent_ro;
GRANT USAGE ON SCHEMA serving TO agent_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA serving TO agent_ro;
-- Tables created later by sync_writer (including the rename-based atomic swap) are readable at once.
ALTER DEFAULT PRIVILEGES FOR ROLE sync_writer IN SCHEMA serving GRANT SELECT ON TABLES TO agent_ro;

-- Exactly three role settings for agent_ro (the contract lists nothing else).
ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET statement_timeout = '30s';
ALTER ROLE agent_ro SET search_path = serving;

-- sync_writer may use pgvector/citext types etc. through the extensions in public; extensions
-- installed in public need USAGE on public for the roles that use their types.
GRANT USAGE ON SCHEMA public TO app_owner, sync_writer;
