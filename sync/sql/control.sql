-- Control tables of the sync worker (specs/004-sales-contract-sync, data-model.md).
-- Idempotent: applied by the worker at every start, as role sync_writer (owner of schema serving).

CREATE TABLE IF NOT EXISTS serving.sync_state (
  table_name           text PRIMARY KEY,
  definition_hash      text,
  data_as_of           timestamptz,
  last_run_id          bigint,
  last_outcome         text CHECK (last_outcome IN ('succeeded', 'failed', 'refused')),
  last_reason          text,
  source_rows          integer,
  copied_rows          integer,
  consecutive_failures integer NOT NULL DEFAULT 0,
  stale_after_seconds  integer NOT NULL DEFAULT 1800,
  failing_after        integer NOT NULL DEFAULT 3,
  updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS serving.sync_runs (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  table_name    text NOT NULL,
  trigger       text NOT NULL CHECK (trigger IN ('schedule', 'admin', 'command')),
  requested_by  uuid,
  started_at    timestamptz NOT NULL,
  finished_at   timestamptz,
  outcome       text NOT NULL CHECK (outcome IN ('running', 'succeeded', 'failed', 'refused')),
  reason_code   text CHECK (reason_code IN (
                  'zero_rows', 'below_fraction', 'below_minimum', 'duplicate_key', 'key_missing',
                  'shape_changed', 'value_out_of_range', 'source_unreachable', 'read_timeout',
                  'login_failed', 'privileged_account', 'swap_failed', 'source_not_configured',
                  'interrupted', 'unexpected')),
  source_rows   integer,
  copied_rows   integer,
  duration_ms   integer,
  error_text    text CHECK (char_length(error_text) <= 500),
  warnings      jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS sync_runs_table_id_idx ON serving.sync_runs (table_name, id DESC);

CREATE TABLE IF NOT EXISTS serving.sync_requests (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  table_name    text NOT NULL,
  requested_by  uuid NOT NULL,
  requested_at  timestamptz NOT NULL DEFAULT now(),
  state         text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'taken', 'cancelled')),
  run_id        bigint
);
CREATE INDEX IF NOT EXISTS sync_requests_pending_idx ON serving.sync_requests (table_name, state, requested_at);

-- The API (role api_rw) may read the state and the history and record a request. Nothing else.
GRANT SELECT ON serving.sync_state, serving.sync_runs TO api_rw;
GRANT SELECT, INSERT ON serving.sync_requests TO api_rw;
GRANT USAGE ON SEQUENCE serving.sync_requests_id_seq TO api_rw;

-- The agent only needs sync_state (for data_as_of). Default privileges grant SELECT on every new table
-- to agent_ro, so the other two are revoked here, on every apply.
REVOKE SELECT ON serving.sync_runs, serving.sync_requests FROM agent_ro;
