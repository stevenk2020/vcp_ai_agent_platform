-- migrate:up

CREATE TABLE app.users (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email             text NOT NULL UNIQUE CHECK (email = lower(email)),
    name              text,
    role              text NOT NULL DEFAULT 'user' CHECK (role IN ('admin','builder','user')),
    status            text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','suspended','rejected')),
    suspended_reason  text CHECK (suspended_reason IN ('admin','allowlist_removed')),
    last_login_at     timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CHECK ((status = 'suspended') = (suspended_reason IS NOT NULL))
);

CREATE TABLE app.allowlist (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind          text NOT NULL CHECK (kind IN ('email','domain')),
    value         text NOT NULL CHECK (value = lower(value)),
    default_role  text NOT NULL CHECK (default_role IN ('admin','builder','user')),
    added_by      uuid REFERENCES app.users(id) ON DELETE SET NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, value),
    CHECK (kind <> 'email' OR (length(value) - length(replace(value, '@', ''))) = 1),
    CHECK (kind <> 'domain' OR position('@' in value) = 0)
);

CREATE TABLE app.email_codes (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text NOT NULL CHECK (email = lower(email)),
    code_hmac     bytea NOT NULL,
    expires_at    timestamptz NOT NULL,
    attempts      integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
    consumed_at   timestamptz,
    cancelled_at  timestamptz,
    requested_ip  inet,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX email_codes_email_created_idx ON app.email_codes (email, created_at DESC);

CREATE TABLE app.sessions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    token_hash      bytea NOT NULL UNIQUE,
    expires_at      timestamptz NOT NULL,
    revoked_at      timestamptz,
    revoked_reason  text CHECK (revoked_reason IN ('signed_out','allowlist_removed','rejected','suspended')),
    ip              inet,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sessions_open_by_user_idx ON app.sessions (user_id) WHERE revoked_at IS NULL;

CREATE TABLE app.audit_events (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at             timestamptz NOT NULL,
    actor_user_id  uuid,
    actor_email    text,
    action         text NOT NULL,
    target         text,
    outcome        text NOT NULL CHECK (outcome IN ('success','failure')),
    ip             inet,
    detail         jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX audit_events_action_idx ON app.audit_events (action, id DESC);

-- The API role gets exactly these rights; there are no default privileges for future tables.
GRANT SELECT, INSERT, UPDATE, DELETE ON app.users, app.allowlist, app.email_codes, app.sessions TO api_rw;
GRANT SELECT, INSERT ON app.audit_events TO api_rw;
GRANT USAGE ON SEQUENCE app.audit_events_id_seq TO api_rw;

-- migrate:down

DROP TABLE IF EXISTS app.audit_events;
DROP TABLE IF EXISTS app.sessions;
DROP TABLE IF EXISTS app.email_codes;
DROP TABLE IF EXISTS app.allowlist;
DROP TABLE IF EXISTS app.users;
