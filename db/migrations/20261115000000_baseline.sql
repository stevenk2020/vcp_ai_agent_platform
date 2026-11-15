-- migrate:up
CREATE TABLE IF NOT EXISTS app.platform_meta (
    key        text PRIMARY KEY,
    value      text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO app.platform_meta (key, value)
VALUES ('schema_generation', '1')
ON CONFLICT (key) DO NOTHING;

-- migrate:down
DROP TABLE IF EXISTS app.platform_meta;
