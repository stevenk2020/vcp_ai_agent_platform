# Data Model: Passwordless Sign-in with Admin Allowlist

All tables live in schema `app` and are created by dbmate migrations run as `app_owner`. Every table
has `id uuid` (default `gen_random_uuid()`) unless noted, `created_at timestamptz not null default now()`
and, where rows change, `updated_at timestamptz not null default now()`. Time-based rules use
timestamps supplied by the application clock (so tests can advance time), not `now()` in queries.

## Entities

### `app.users`

| Column | Type | Rules |
| --- | --- | --- |
| `email` | text | not null, unique, `CHECK (email = lower(email))`, trimmed |
| `name` | text | nullable (not collected in this feature) |
| `role` | text | not null, `CHECK (role IN ('admin','builder','user'))`, default `'user'` |
| `status` | text | not null, `CHECK (status IN ('pending','active','suspended','rejected'))`, default `'pending'` |
| `suspended_reason` | text | nullable, `CHECK (suspended_reason IN ('admin','allowlist_removed'))` and `CHECK ((status = 'suspended') = (suspended_reason IS NOT NULL))` |
| `last_login_at` | timestamptz | nullable |

This table is also the **approval queue**: the queue is `status = 'pending'` ordered by `created_at`
(spec entity "Approval request" has no table of its own).

State transitions:

```text
(new email proven) ──allowlisted──────────────▶ active  (role = entry default)
                  └─not allowlisted───────────▶ pending
pending ──admin approve(role)──▶ active        pending ──admin reject──▶ rejected
pending ──email/domain added to allowlist and next sign-in──▶ active
active  ──admin suspend──▶ suspended (reason admin)
active  ──allowlist entry removed (no other entry matches)──▶ suspended (reason allowlist_removed, sessions revoked)
suspended (reason allowlist_removed) ──a matching entry exists at next sign-in──▶ active (entry role)
suspended (reason admin) ──admin approve(role)──▶ active   rejected ──admin approve(role)──▶ active
```

Rules: a person suspended with reason `admin`, or rejected, stays blocked even if an allowlist entry
matches; approving anyone clears `suspended_reason`. Removing an allowlist entry suspends only active users whose email matches that entry and no
other entry (a person approved by hand whose email matches neither is not affected); the last active
admin cannot be suspended, rejected, demoted or left without access (`409 last_admin`).

### `app.allowlist`

| Column | Type | Rules |
| --- | --- | --- |
| `kind` | text | not null, `CHECK (kind IN ('email','domain'))` |
| `value` | text | not null, unique with kind, `CHECK (value = lower(value))`; an email must contain one `@`, a domain must not contain `@` |
| `default_role` | text | not null, `CHECK (default_role IN ('admin','builder','user'))` |
| `added_by` | uuid | references `app.users(id)` on delete set null |

Matching: exact email entry first, else exact domain entry (no subdomains).

### `app.email_codes`

| Column | Type | Rules |
| --- | --- | --- |
| `email` | text | not null, lowercase |
| `code_hmac` | bytea | not null; `HMAC-SHA256(pepper, email:id:code)`, never the digits |
| `expires_at` | timestamptz | not null; issue time + 10 minutes |
| `attempts` | integer | not null default 0, `CHECK (attempts BETWEEN 0 AND 5)` |
| `consumed_at` | timestamptz | nullable; set on first successful use |
| `cancelled_at` | timestamptz | nullable; set when a newer code is issued, after 5 wrong attempts, or when sending fails |
| `requested_ip` | inet | nullable |

Index: `(email, created_at DESC)`. A code is usable only if `consumed_at`, `cancelled_at` are null,
`expires_at` is in the future and `attempts < 5`. Rows older than 7 days can be purged (a cleanup is a
later concern).

### `app.sessions`

| Column | Type | Rules |
| --- | --- | --- |
| `user_id` | uuid | not null, references `app.users(id)` on delete cascade |
| `token_hash` | bytea | not null, unique; `SHA-256(token)`, never the token |
| `expires_at` | timestamptz | not null; sign-in time + 7 days |
| `revoked_at` | timestamptz | nullable |
| `revoked_reason` | text | nullable: `signed_out`, `allowlist_removed`, `rejected` or `suspended` |
| `ip` | inet | nullable |

Index: `(user_id) WHERE revoked_at IS NULL`. A session is valid when `revoked_at IS NULL`,
`expires_at` is in the future and the user's `status = 'active'`.

### `app.audit_events` (append-only for the API role)

| Column | Type | Rules |
| --- | --- | --- |
| `id` | bigint | generated identity primary key (not uuid, for cheap ordering) |
| `at` | timestamptz | not null |
| `actor_user_id` | uuid | nullable (system or unknown actor) |
| `actor_email` | text | nullable |
| `action` | text | not null; one of the names below |
| `target` | text | nullable (an email, entry id, or user id) |
| `outcome` | text | not null, `CHECK (outcome IN ('success','failure'))` |
| `ip` | inet | nullable |
| `detail` | jsonb | not null default `'{}'`; never contains codes or tokens |

Action names: `code_requested`, `code_send_failed`, `code_verified`, `code_failed`, `code_locked`,
`google_signin`, `signin_pending`, `signin_refused`, `rate_limited`, `session_created`,
`session_revoked`, `signed_out`, `allowlist_added`, `allowlist_removed`, `user_approved`,
`user_rejected`, `user_suspended`, `role_changed`, `first_admin_created`, `first_admin_refused`, `last_admin_blocked`.

## Privileges (extends the feature 001 contract)

| Role | `app.users` | `app.allowlist` | `app.email_codes` | `app.sessions` | `app.audit_events` | `serving`, `kb` |
| --- | --- | --- | --- | --- | --- | --- |
| `api_rw` | SELECT, INSERT, UPDATE, DELETE | SELECT, INSERT, UPDATE, DELETE | SELECT, INSERT, UPDATE, DELETE | SELECT, INSERT, UPDATE, DELETE | SELECT, INSERT | none |
| `app_owner` | owner | owner | owner | owner | owner | none |
| `agent_ro`, `sync_writer` | none | none | none | none | none | unchanged |
| `backup_ro` | read (all) | read (all) | read (all) | read (all) | read (all) | unchanged |

`api_rw` also gets `USAGE` on schema `app`, and `CONNECT` on the database (bootstrap). Sequences for
`audit_events` need `USAGE` for `api_rw`.

## Secrets (extends the feature 001 table)

| Secret | Consumers |
| --- | --- |
| `api_rw_password` | `db-init`, `api`, `api-test` |
| `auth_pepper` | `api`, `api-test` |
| `api_external` (SMTP and Google settings, dotenv) | `api`, `api-test` |
| `app_owner_password` | also `api-test` (test cleanup only) |

## Configuration (non-secret, environment)

| Name | Meaning | Default |
| --- | --- | --- |
| `APP_ENV` | `development` or `production` | `development` |
| `MAIL_BACKEND` | `capture`, `smtp` or `disabled` | `capture` (refused when `APP_ENV=production`; `disabled` turns email codes off, Google only) |
| `PUBLIC_ORIGIN` | origin allowed for state-changing requests and Google redirect | `https://vcp.onepercentdata.site` |
| `CODE_RATE_PER_EMAIL` | codes per email per hour | `5` |
| `CODE_RATE_PER_IP` | codes per IP per hour | `20` |
| `VERIFY_RATE_PER_IP` | verify attempts per IP per 10 minutes | `30` |
| `SESSION_DAYS` | session lifetime | `7` |
| `MAIL_CAPTURE_TTL_MINUTES` | capture sink files older than this are deleted (development only) | `60` |
