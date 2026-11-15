-- migrate:up

-- Feature 003: the optional access request a waiting person submits once (name, reason).
-- `users.name` already exists; it now holds the display name from that request.
ALTER TABLE app.users
    ADD COLUMN request_reason       text CHECK (request_reason IS NULL OR char_length(request_reason) BETWEEN 1 AND 500),
    ADD COLUMN request_submitted_at timestamptz,
    ADD CONSTRAINT users_name_length CHECK (name IS NULL OR char_length(name) BETWEEN 1 AND 80);

-- migrate:down

ALTER TABLE app.users
    DROP CONSTRAINT users_name_length,
    DROP COLUMN request_submitted_at,
    DROP COLUMN request_reason;
