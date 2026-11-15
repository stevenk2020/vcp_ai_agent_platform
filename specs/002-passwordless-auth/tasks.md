---
description: "Task list for 002-passwordless-auth"
---

# Tasks: Passwordless Sign-in with Admin Allowlist

**Input**: Design documents from `/specs/002-passwordless-auth/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md; feature 001 running (`make up`)

**Tests**: Included. Constitution Development Workflow requires failure-case tests for security-relevant code, and the spec's acceptance criteria are tests.

**Organization**: Grouped by user story (US1 to US7 from spec.md). All paths are relative to the repository root.

**Acceptance-test convention** (constitution, Development Workflow): each user story phase has a Tests block that is the acceptance test for every task in that phase. Documentation and `[MANUAL]` tasks are accepted by the check named in the task itself or by the phase test that exercises them.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 to US7, only in user-story phases
- **[MANUAL]**: needs the operator (accounts, credentials, production switch); cannot be automated by the coding agent

**How tests run**: integration tests run in the `api-test` container: `make test-api` (subset: `make test-api ARGS="-k signin"`; never `make test-api -- -k ...`). The feature 001 suite still runs with `make test`. Both must stay green; `make scan-secrets` must stay clean.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Package skeleton, image, secrets and tooling for the new `api` service

- [X] T001 Create `api/pyproject.toml` (Python `>=3.12`; dependencies `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `psycopg[binary,pool]>=3.2`, `redis>=5`, `email-validator`, `google-auth`, `httpx`; optional group `dev` with `pytest`, `pytest-asyncio`; pytest config `asyncio_mode = "auto"`, `testpaths = ["tests"]`) and the empty packages `api/app/__init__.py`, `api/app/auth/__init__.py`, `api/app/admin/__init__.py`, `api/tests/__init__.py`, `api/tests/unit/__init__.py`, `api/tests/integration/__init__.py`
- [X] T002 Create `api/Dockerfile` (multi-stage on `python:3.12-slim`, non-root user, dependencies installed from `pyproject.toml`; target `runtime` starts `uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'`; target `test` adds the `dev` group and copies `api/tests`) and `api/.dockerignore` (`.venv`, `__pycache__`, `.pytest_cache`; the runtime stage copies only `app/`, the test stage also copies `tests/`)
- [X] T003 [P] Update `.gitignore`: add `api/.venv/`, `api/.pytest_cache/`, `*.egg-info/`
- [X] T004 Update `scripts/gen-secrets.sh`: also create `api_rw_password` and `auth_pepper` (random, same method as the other passwords, never overwrite, mode 600, names only in output) and create `secrets/api_external` from a template only if missing (dotenv, mode 600, keys with empty values: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`); update the table in `secrets/README.md` with the three new files and their consumers (`api_rw_password`: `db-init`, `api`, `api-test`; `auth_pepper`: `api`, `api-test`; `api_external`: `api`, `api-test`, operator-filled)
- [X] T005 [P] Make `scripts/scan-secrets.sh` and `_secret_values()` in `tests/foundation/test_secrets_scope.py` treat `secrets/api_external` like `rclone_config`: only the values of keys whose name contains `password`, `secret`, `token` or `key` and are 8 or more characters count (empty template values never count)
- [X] T006 [P] Create `scripts/test-api.sh` and Makefile target `test-api` (`@bash scripts/test-api.sh`, ARGS passed through the environment, usage `make test-api ARGS="-k signin"`): the script starts an isolated Compose project (`-p platform-apitest`, `POSTGRES_PORT=55433`, same secret files) with only `postgres`, `redis`, `db-init` and `migrate`, waits for them with `scripts/wait-healthy.sh` (extend that script to honour an optional `WAIT_SERVICES` variable listing the services to check), runs `docker compose -p platform-apitest --profile test run --rm -e API_TEST_ISOLATED=1 api-test pytest $ARGS`, always removes the project with `down -v --remove-orphans` (also on failure or interruption) and exits with pytest's exit code; it must refuse to run against a project named `platform`; add `test-api` to `.PHONY`; the script fails with a clear message if the `api-test` service does not exist yet

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Database role and tables, compose services, proxy route, and the shared building blocks every story uses

**⚠️ CRITICAL**: No user story work starts until this phase is complete

- [X] T007 Add role `api_rw` to the bootstrap: in `db/bootstrap/20-schemas-roles.sh` read `/run/secrets/api_rw_password`, create the role `LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE` and set its password through `\getenv` like the other roles; in `db/bootstrap/30-privileges.sql` `GRANT CONNECT` on the current database to `api_rw`, `GRANT USAGE ON SCHEMA app TO api_rw`, and make sure `api_rw` has no privilege on schemas `serving`, `kb` or `public` and no role setting; add secret `api_rw_password` to the `db-init` service and the top-level `secrets:` list in `compose.yaml`
- [X] T008 Create `db/migrations/20261115010000_auth_tables.sql` (dbmate up/down) creating in schema `app`, each with `id uuid PRIMARY KEY DEFAULT gen_random_uuid()` (except `audit_events`), `created_at timestamptz NOT NULL DEFAULT now()` and where rows change `updated_at timestamptz NOT NULL DEFAULT now()`:
  - `users`: `email text NOT NULL UNIQUE CHECK (email = lower(email))`, `name text`, `role text NOT NULL DEFAULT 'user' CHECK (role IN ('admin','builder','user'))`, `status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','active','suspended','rejected'))`, `suspended_reason text CHECK (suspended_reason IN ('admin','allowlist_removed')) CHECK ((status = 'suspended') = (suspended_reason IS NOT NULL))`, `last_login_at timestamptz`
  - `allowlist`: `kind text NOT NULL CHECK (kind IN ('email','domain'))`, `value text NOT NULL CHECK (value = lower(value))`, `default_role text NOT NULL CHECK (default_role IN ('admin','builder','user'))`, `added_by uuid REFERENCES app.users(id) ON DELETE SET NULL`, `UNIQUE (kind, value)`, plus checks that an email entry contains exactly one `@` and a domain entry contains none
  - `email_codes`: `email text NOT NULL CHECK (email = lower(email))`, `code_hmac bytea NOT NULL`, `expires_at timestamptz NOT NULL`, `attempts integer NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5)`, `consumed_at timestamptz`, `cancelled_at timestamptz`, `requested_ip inet`, index `(email, created_at DESC)`
  - `sessions`: `user_id uuid NOT NULL REFERENCES app.users(id) ON DELETE CASCADE`, `token_hash bytea NOT NULL UNIQUE`, `expires_at timestamptz NOT NULL`, `revoked_at timestamptz`, `revoked_reason text CHECK (revoked_reason IN ('signed_out','allowlist_removed','rejected','suspended'))`, `ip inet`, index `(user_id) WHERE revoked_at IS NULL`
  - `audit_events`: `id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY`, `at timestamptz NOT NULL`, `actor_user_id uuid`, `actor_email text`, `action text NOT NULL`, `target text`, `outcome text NOT NULL CHECK (outcome IN ('success','failure'))`, `ip inet`, `detail jsonb NOT NULL DEFAULT '{}'`
  - grants to `api_rw`: `SELECT, INSERT, UPDATE, DELETE` on `users`, `allowlist`, `email_codes`, `sessions`; only `SELECT, INSERT` on `audit_events`; `USAGE` on the `audit_events` identity sequence; no default privileges
- [X] T009 Extend the feature 001 tests for the new role and tables: add `"api_rw": "api_rw_password"` to `ROLE_SECRETS` in `tests/foundation/conftest.py` and an `api_rw` fixture; add `api_rw` to `ALL_ROLES` in `tests/foundation/test_permissions.py` (cannot create in `public`, cannot create temp tables, cannot create roles or extensions) and to the role list in `test_roles_are_not_privileged` in `tests/foundation/test_extensions.py`; write `tests/foundation/test_auth_schema.py` checking: `api_rw` can read and write `app.users`, `app.allowlist`, `app.email_codes`, `app.sessions`; can `SELECT` and `INSERT` but not `UPDATE`, `DELETE` or `TRUNCATE` on `app.audit_events`; cannot read `serving` or `kb`; each CHECK constraint from T008 rejects a bad row (uppercase email, role `root`, status `banned`, `attempts = 6`, outcome `maybe`, allowlist email without `@`, domain with `@`, bad `revoked_reason`, a suspended user without a reason, a reason on a non-suspended user); and add an `api_rw` row to the matrix in `specs/001-foundation-data-layer/contracts/database-access.md`
- [X] T010 Add the `api` service to `compose.yaml`: `build: {context: ./api, target: runtime}`, `restart: unless-stopped`, no `ports`, `depends_on` `migrate` with `condition: service_completed_successfully` and `redis` healthy, environment `POSTGRES_HOST=postgres`, `POSTGRES_DB`, `REDIS_URL=redis://redis:6379/0`, `APP_ENV=${APP_ENV:-development}`, `MAIL_BACKEND=${MAIL_BACKEND:-capture}`, `PUBLIC_ORIGIN=${PUBLIC_ORIGIN:-https://vcp.onepercentdata.site}`, `CODE_RATE_PER_EMAIL`, `CODE_RATE_PER_IP`, `VERIFY_RATE_PER_IP`, `SESSION_DAYS` and `MAIL_CAPTURE_TTL_MINUTES` with the defaults from data-model.md "Configuration", secrets `api_rw_password`, `auth_pepper`, `api_external`, volume `mail_capture:/var/mail-capture`, network `platform`, and a healthcheck that calls `GET /v1/health` (python one-liner, no extra packages); add the `api-test` service under `profiles: ["test"]` with `target: test`, the same environment, the secrets above plus `app_owner_password` (test cleanup only), the same network, `entrypoint: ["python", "-m"]` so `pytest` can be passed as the command, environment `API_TEST_ISOLATED=${API_TEST_ISOLATED:-0}`; declare top-level secrets `auth_pepper` and `api_external` (files in `./secrets/`) and volume `mail_capture`; extend `EXPECTED_SECRETS` in `tests/foundation/test_secrets_scope.py` with `"api": {"api_rw_password", "auth_pepper", "api_external"}` and `"api-test": {"api_rw_password", "auth_pepper", "api_external", "app_owner_password"}` and make its config helper include `--profile test`
- [X] T011 [P] Update `caddy/Caddyfile`: in the site block add `handle /v1/* { reverse_proxy api:8000 }` before the placeholder responses (Caddy replaces `X-Forwarded-For` with the client address, do not add `trusted_proxies`); add `depends_on` `api` with `condition: service_healthy` to the `caddy` service in `compose.yaml`
- [X] T012 Implement settings and clock: `api/app/config.py` (Pydantic settings from environment and `/run/secrets/*`: DB host, db name, `api_rw` password, Redis URL, `auth_pepper`, the `api_external` dotenv values, `APP_ENV`, `MAIL_BACKEND`, `PUBLIC_ORIGIN`, rate limits and `SESSION_DAYS` with the defaults from data-model.md; refuse to start when `APP_ENV=production` and `MAIL_BACKEND=capture`, or when `MAIL_BACKEND=smtp` and the SMTP settings are empty; `MAIL_BACKEND=disabled` is allowed in production only when `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are non-empty, otherwise refuse to start because nobody could sign in) and `api/app/clock.py` (`Clock` protocol with `now()`, `SystemClock`, `FakeClock` with `advance(seconds)`); write `api/tests/unit/test_config.py` covering the three refusals (capture in production, smtp without settings, disabled mail without Google in production), the allowed combinations, defaults, and empty `api_external` values meaning "not configured"
- [X] T013 [P] Implement `api/app/security.py`: `new_token()` (32 random bytes, base64url), `hash_token()` (SHA-256 bytes), `new_code()` (`secrets.randbelow(10**6)` zero-padded to 6 digits), `code_hmac(pepper, email, code_id, code)` (`HMAC-SHA256` over `email:code_id:code`), `constant_time_equal()`, `normalize_email()` (trim, lowercase, `email-validator` syntax check without DNS lookup, at most 254 characters); write `api/tests/unit/test_security.py` (token length and uniqueness, code always 6 digits, HMAC differs for different email, id or pepper, comparison is constant-time helper, normalisation of `  Alice@Example.COM ` and rejection of malformed addresses)
- [X] T014 [P] Implement `api/app/audit.py`: `record(conn, clock, action, outcome, actor_user_id=None, actor_email=None, target=None, ip=None, detail=None)` inserting into `app.audit_events` and rejecting a `detail` containing keys such as `code`, `token`, `password`, `secret`, `cookie`, `authorization`; plus a logging filter that drops those field names from structured logs and a `configure_logging()` that installs it; write `api/tests/unit/test_audit_scrub.py` (forbidden keys raise, scrubber removes them from log records, normal fields pass)
- [X] T015 [P] Implement `api/app/rate_limit.py`: fixed-window counters with Redis `INCR` and `EXPIRE` where the window number `floor(now / window)` comes from the injected `Clock` and is part of the key (`hit(clock, key, limit, window_seconds)` returns allowed flag and seconds until the window ends), key helpers `code_email_key(email)` (SHA-256 of the email, never the address), `code_ip_key(ip)`, `verify_ip_key(ip)`; write `api/tests/unit/test_rate_limit.py` against the real Redis in the compose network (limit reached on the next call, the window resets when the FakeClock moves to the next window, keys never contain the plain email)
- [X] T016 [P] Implement `api/app/mailer.py`: `Mailer` protocol (`send_code(to, code, minutes)`), `CaptureMailer` (writes one JSON file per message into `/var/mail-capture`, or a given directory in tests), `SmtpMailer` (`smtplib` with STARTTLS, run in a thread, sender and credentials from settings), `DisabledMailer` (raises a not-configured error, writes nothing) and `build_mailer(settings)`; the message body is Vietnamese followed by English and contains the code and its 10-minute lifetime, and the code is never logged; the capture backend deletes files older than `MAIL_CAPTURE_TTL_MINUTES` at startup and at every write; write `api/tests/unit/test_mailer.py` (the disabled mailer raises and writes nothing, capture file content, old capture files are deleted after the TTL using the FakeClock, the body contains both languages, SMTP backend builds a correct message and raises on connection errors using a monkeypatched `smtplib.SMTP`)
- [X] T017 Implement `api/app/db.py` (async `psycopg_pool` connected as `api_rw`, `transaction()` helper) and `api/app/main.py` (`create_app()` factory taking optional overrides for clock, mailer, identity provider and settings; routers registered later; JSON error handler producing `{"error", "message"}`; middleware refusing unsafe methods (`POST`, `PUT`, `PATCH`, `DELETE`) without `Origin` equal to `PUBLIC_ORIGIN` with `403 origin_not_allowed`, except the Google callback which is a `GET`; `GET /v1/health` checking Postgres and Redis and returning `{"status": "ok", "mail_backend": ...}`); create `api/tests/conftest.py` with fixtures: settings, `FakeClock`, `CaptureMailer` in a temp dir, a fake identity provider placeholder, DB cleanup through `app_owner` (truncate the five auth tables between tests) guarded so that the whole suite refuses to start unless `API_TEST_ISOLATED=1` and the database host belongs to the `platform-apitest` project, a `captured_logs` fixture collecting everything the app logs during a test, an httpx `AsyncClient` over `ASGITransport` with base URL `https://test` and the `Origin` header preset, and helpers `add_allowlist(...)`, `read_code(email)`; write `api/tests/integration/test_app.py` (health `200`, health `503` when Redis is unreachable, unsafe request without `Origin` gets `403`, error shape)
- [X] T018 Run the foundation: `make secrets`, `make up` (the new role, migration and `api` service must come up healthy; confirm `scripts/wait-healthy.sh` handles the `api` healthcheck), `make test` (feature 001 suite incl. the extended tests), `make test-api` (it runs in the isolated project and must leave the live database untouched: check the live `app.users` row count before and after); fix until green, then `make scan-secrets` and commit

**Checkpoint**: Role, tables and service exist; foundation tests and the 001 suite pass

---

## Phase 3: User Story 1 - An approved person signs in with an emailed code (Priority: P1) 🎯 MVP

**Goal**: An allowlisted email can request a code, verify it, get a session cookie, call a protected endpoint and sign out.

**Independent Test**: With one allowlisted email inserted by the test fixture and the capture mailer, request and verify a code, call `GET /v1/me`, sign out, and see `GET /v1/me` refused.

### Tests for User Story 1

- [X] T019 [P] [US1] Write `api/tests/integration/test_signin_code.py` covering: request returns `202` with the documented message and one captured mail; verify with the correct code returns `200 signed_in`, the role from the entry, and a `Set-Cookie` that contains `__Host-session`, `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age` equal to `SESSION_DAYS * 86400` (604800 by default) and no `Domain`; `GET /v1/me` returns the user; the same code a second time returns `401 invalid_or_expired_code`; a code is refused after the fake clock advances 10 minutes and one second; 5 wrong attempts cancel the code so the correct digits then return `401`; the 4th wrong attempt followed by the correct code still succeeds; requesting a second code cancels the first; email with spaces and capitals resolves to the same user; two verifies of one code at the same moment succeed once (run concurrently); a session is refused after the fake clock advances 7 days; `POST /v1/auth/logout` returns `204`, clears the cookie, and `GET /v1/me` is then `401`; a failing mailer returns `503 try_again_later`, leaves no usable code and audits `code_send_failed`; with `MAIL_BACKEND=disabled` the request returns `503 not_configured`, sends and stores nothing, and `GET /v1/health` reports `mail_backend: disabled`

### Implementation for User Story 1

- [X] T020 [US1] Implement `api/app/auth/service.py`: `request_code(email, ip)` (when `MAIL_BACKEND=disabled` raise the not-configured error first, before any database, Redis or mail work; otherwise normalise, cancel earlier unused codes for the email, insert the new code with `id`, `code_hmac`, `expires_at = now + 10 minutes` from the injected clock, send through the mailer, on mail failure cancel the new code, audit, and raise the "try again later" error; audits `code_requested`); the attempt counter, the cancellation at the fifth failure and the audit record are committed before the `401` is returned (not inside a transaction the error would roll back); `verify_code(email, code, ip)` (inside one transaction `SELECT ... FOR UPDATE` the newest code for the email that has `consumed_at IS NULL`, `cancelled_at IS NULL`, `expires_at > now`, `attempts < 5`; compare the HMAC in constant time; on mismatch increment `attempts`, cancel at 5 (audit `code_failed`, `code_locked`) and return the generic invalid result; on match set `consumed_at`); after a correct code the allowlist gate for this story: find the user or an allowlist entry (exact email entry first, else exact domain entry), create or load the user as `active` with the entry's default role and set `last_login_at`, create the session (`new_token`, store `hash_token`, `expires_at = now + SESSION_DAYS`), audit `code_verified` and `session_created`; for an unknown, non-allowlisted email return the placeholder outcome `pending` without creating a user (completed in US3)
- [X] T021 [P] [US1] Implement `api/app/auth/deps.py`: `current_user` dependency that reads `__Host-session`, looks up `hash_token(token)` in `app.sessions`, requires `revoked_at IS NULL`, `expires_at > now` and the user's `status = 'active'`, loads the user's current role, and otherwise raises `401 unauthenticated`; `require_role(*roles)` raising `403 forbidden`
- [X] T022 [US1] Implement `api/app/auth/routes.py` and register it in `api/app/main.py`: `POST /v1/auth/code/request` (`503 not_configured` when email codes are disabled), `POST /v1/auth/code/verify` (sets the cookie exactly as in the contract), `POST /v1/auth/logout` (revokes the current session with reason `signed_out`, clears the cookie, audits `signed_out`, `204`), `GET /v1/me`; request bodies are Pydantic models that forbid unknown fields; `code` must be exactly 6 digits
- [X] T023 [US1] Run `make test-api ARGS="-k signin"`, fix until green, run the whole `make test-api`, commit

**Checkpoint**: US1 independently demonstrable; MVP of the feature

---

## Phase 4: User Story 2 - An admin decides who gets in and can cut access at once (Priority: P1)

**Goal**: Admin API for the allowlist, the approval queue, role and status changes, with immediate session revocation.

**Independent Test**: As an admin add an email, sign in as it, remove the entry and see the existing session fail on the next request; approve and reject a pending person; a non-admin gets `403`.

### Tests for User Story 2

- [X] T024 [P] [US2] Write `api/tests/integration/test_admin.py` covering every row of the admin tables in `specs/002-passwordless-auth/contracts/http-api.md`: add email and domain entries (`201`, duplicate `409 conflict`, malformed value `422`, uppercase value stored lowercase), list shows who added and when, an email entry beats a domain entry, a domain entry does not match a subdomain; a signed-in `user` and `builder` get `403` on every admin route and an anonymous caller gets `401`; admin routes require the `Origin` header (`403` without); removing an entry revokes the sessions of users who matched only that entry (their next `GET /v1/me` is `401`, status `suspended`, sessions marked `allowlist_removed`) while a user matched by another entry keeps working; approve (with a role), reject, suspend, role change (applies on the next request); `409 last_admin` when removing the entry of, suspending, rejecting or demoting the only active admin (and the same when two admins act at the same moment); `GET /v1/admin/users?status=pending` filter

### Implementation for User Story 2

- [X] T025 [US2] Implement `api/app/admin/service.py`: allowlist add, list, remove; user list with optional status filter; approve (set `active` and role), reject (set `rejected`), suspend (stores `suspended_reason = 'admin'`), change role; removal of an allowlist entry stores `suspended_reason = 'allowlist_removed'`; approve clears `suspended_reason`; every state change that removes access sets `revoked_at` and `revoked_reason` on that user's open sessions in the same transaction (`allowlist_removed`, `rejected`, `suspended`); last-admin protection using `pg_advisory_xact_lock` and a count of remaining `active` admins, raising the `last_admin` conflict (and auditing `last_admin_blocked`); removal of an allowlist entry suspends only active users whose email matches that entry and no other entry; audits `allowlist_added`, `allowlist_removed`, `user_approved`, `user_rejected`, `user_suspended`, `role_changed`, `session_revoked` with actor and IP
- [X] T026 [US2] Implement `api/app/admin/routes.py` with the admin endpoints from the contract (all guarded by `require_role("admin")`) and register the router in `api/app/main.py`; update `verify_code` in `api/app/auth/service.py` so a user whose `status` is `active` signs in with their stored role even without an allowlist entry (approved by hand)
- [X] T027 [US2] Run `make test-api ARGS="-k admin"`, then the whole `make test-api`; fix; commit

**Checkpoint**: US1 and US2 work together; access can be granted and cut

---

## Phase 5: User Story 3 - Unapproved people are kept out without learning who is approved (Priority: P1)

**Goal**: Non-allowlisted people prove their email, land in the queue with no session, and the form never reveals who is allowed.

**Independent Test**: Compare responses and timing for an allowlisted and a non-allowlisted email; the second ends up pending with no access.

### Tests for User Story 3

- [X] T028 [P] [US3] Write `api/tests/integration/test_unapproved.py` covering: `POST /v1/auth/code/request` returns byte-identical status, headers (except `Date`) and body for allowlisted and non-allowlisted addresses; the number of mails sent is the same; over 200 alternating trials the difference between the two median response times is under 20 percent; a non-allowlisted person who enters a valid code gets `200 {"status": "pending"}` with no `Set-Cookie`, a `pending` user row and one entry in `GET /v1/admin/users?status=pending`; signing in again while pending adds no second entry and still gives no session; a pending person's `GET /v1/me` is `401`; adding an entry that matches a pending person lets their next sign-in succeed without approval; removing an entry and adding it back lets the person sign in again (old sessions stay revoked); a person suspended by an admin, or rejected, stays blocked at the next sign-in even when an entry matches, until an admin approves them; a user suspended by an admin or rejected gets `200 {"status": "refused"}` even when allowlisted, while a user suspended only by allowlist removal gets a session once an entry matches again; wrong, expired, consumed and unknown-email verifications all return the same `401` body

### Implementation for User Story 3

- [X] T029 [US3] Complete the gate in `api/app/auth/service.py`: for a correct code (or later a verified Google email) with no matching active user and no allowlist entry, create the user as `pending` (or reuse the existing row, idempotent) and return `pending` without a session, auditing `signin_pending`; for `suspended` and `rejected` users return `refused` and audit `signin_refused`; when an allowlist entry now matches a `pending` user, or a `suspended` user whose `suspended_reason` is `allowlist_removed`, activate them with the entry's role (FR-021), clear the reason and continue to the session; a `suspended` user with reason `admin`, and any `rejected` user, stay blocked even when an entry matches; make sure `request_code` never reads the allowlist
- [X] T030 [US3] Run `make test-api ARGS="-k unapproved"`, then the whole `make test-api`; fix; commit

**Checkpoint**: All three P1 stories complete

---

## Phase 6: User Story 4 - Sign in with a Google account (Priority: P2)

**Goal**: "Sign in with Google" ends in the same gate; only verified emails count.

**Independent Test**: With a fake identity provider, a verified allowlisted email gets a session, a verified non-allowlisted one is pending, an unverified one is refused.

### Tests for User Story 4

- [X] T031 [P] [US4] Write `api/tests/integration/test_google.py` using a `FakeIdentityProvider` injected through `create_app()`: `GET /v1/auth/google/start` returns `302` to the provider URL and sets a short-lived HttpOnly state cookie; callback with a verified allowlisted email redirects to `/?signin=ok` and sets the session cookie; verified non-allowlisted gives `/?signin=pending` and a pending user; suspended gives `/?signin=refused`; an unverified email, a wrong or missing `state`, a provider error and an expired callback all give `/?signin=error` with no session; the same email via code and via Google is one user; with no Google client configured both endpoints return `503 not_configured`; no Google token appears in the database or in captured logs

### Implementation for User Story 4

- [X] T032 [US4] Implement `api/app/google.py`: `IdentityProvider` protocol (`authorization_url(state, code_challenge)`, `verify_callback(code, code_verifier)` returning a verified email or raising), the real `GoogleIdentityProvider` using the authorization-code flow with PKCE (`httpx` for the token exchange, `google.oauth2.id_token.verify_oauth2_token` for signature, issuer, audience and expiry, and a hard requirement on `email_verified`), and a `NotConfiguredProvider` used when `GOOGLE_CLIENT_ID` is empty; tokens are discarded right after verification and never logged
- [X] T033 [US4] Add the Google routes to `api/app/auth/routes.py`: `GET /v1/auth/google/start` (state and PKCE verifier in a signed, HttpOnly, short-lived cookie) and `GET /v1/auth/google/callback` (check state, call the provider, then reuse the shared gate from `api/app/auth/service.py`; redirects exactly as in the contract; audits `google_signin`); wire the provider in `create_app()` from the settings
- [X] T034 [US4] Run `make test-api ARGS="-k google"`, then the whole `make test-api`; fix; commit

**Checkpoint**: Both sign-in paths share one gate

---

## Phase 7: User Story 5 - Abuse is limited (Priority: P2)

**Goal**: Caps on codes per email and per IP and on verify attempts per IP, with one generic message.

**Independent Test**: Request codes until the cap trips; no further mail is sent; same response for allowlisted and non-allowlisted addresses.

### Tests for User Story 5

- [X] T035 [P] [US5] Write `api/tests/integration/test_rate_limits.py` covering: the 6th code request for one email within the hour returns `429 rate_limited` with `Retry-After` and sends no mail; the 21st request from one IP returns `429` even for different emails; the message and status are identical for allowlisted and non-allowlisted addresses; after the fake clock passes the window requests work again; the per-IP verify cap (30 per 10 minutes) returns `429`; limits come from settings (override them in a test); the limiter stores no plain email (inspect Redis keys); the client IP is taken from `X-Forwarded-For` and recorded on the code row

### Implementation for User Story 5

- [X] T036 [US5] Wire `api/app/rate_limit.py` into `request_code` and `verify_code` in `api/app/auth/service.py` and the routes (checks run before any mail or database work, `429` with `Retry-After`, audit `rate_limited` committed before the response, one generic message), and take the client IP from the proxy headers (`X-Forwarded-For`, trusted because only Caddy can reach the container)
- [X] T037 [US5] Run `make test-api ARGS="-k rate"`, then the whole `make test-api`; fix; commit

**Checkpoint**: Abuse protection in place

---

## Phase 8: User Story 6 - The first admin can be created exactly once (Priority: P2)

**Goal**: A documented one-time command creates the first admin and cannot be reused to add more.

**Independent Test**: On an empty database the command creates one admin who can sign in; running it again creates nothing.

### Tests for User Story 6

- [X] T038 [P] [US6] Write `api/tests/integration/test_first_admin.py` calling the CLI entry point in-process and as a subprocess: empty system creates one `admin`/`active` user and exits 0, audit `first_admin_created`; the created admin signs in with a code and passes `require_role("admin")`; a second run with another email creates nothing, exits 2 and audits `first_admin_refused`; two simultaneous runs create exactly one admin; a malformed email exits 1; with only a `rejected` admin present the command works again; no environment variable, setting or file in `api/` can create an admin (assert by searching the source for any such path, and by starting the app with odd environment values)

### Implementation for User Story 6

- [X] T039 [US6] Implement `api/app/cli.py` (`python -m app.cli create-first-admin --email ...`) exactly as in `specs/002-passwordless-auth/contracts/operator-cli.md`: one transaction under `pg_advisory_xact_lock`, refuse when any user with role `admin` and status other than `rejected` exists, otherwise insert the admin as `active` and audit with actor `cli`; exit codes 0, 1 and 2 as documented; no other way of creating an admin exists in the code
- [X] T040 [US6] Run `make test-api ARGS="-k first_admin"`, then the whole `make test-api`; do not create a real admin here (that happens once, in T051, on the live database); fix; commit

**Checkpoint**: The system can be bootstrapped

---

## Phase 9: User Story 7 - Sign-in activity is auditable (Priority: P3)

**Goal**: One audit record per event, readable by admins, with no readable code or token anywhere.

**Independent Test**: Perform each audited action once and see exactly one record each; search the database and logs for secrets and find none.

### Tests for User Story 7

- [ ] T041 [P] [US7] Write `api/tests/integration/test_audit.py` covering: each action name in data-model.md "Action names" is produced exactly once by one scripted scenario (parametrised table of scenario step to expected action, actor, outcome and IP); `GET /v1/admin/audit` returns newest first, honours `limit` (1 to 500), `action` and `before`, and requires the admin role; audit rows cannot be updated or deleted by the API role; after a full run of the sign-in, Google and admin flows, search every auth table, `audit_events.detail` and the captured log output for each code, session token and Google token used and find none readable (hashes and HMACs only)

### Implementation for User Story 7

- [ ] T042 [US7] Implement `GET /v1/admin/audit` in `api/app/admin/routes.py` and `api/app/admin/service.py` (query parameters `limit`, `action`, `before`; items without secrets); fill any audit gap the tests expose in `api/app/auth/service.py`, `api/app/admin/service.py` and `api/app/cli.py` so every action name in data-model.md is recorded once
- [ ] T043 [US7] Run `make test-api ARGS="-k audit"`, then the whole `make test-api`; fix; commit

**Checkpoint**: All seven user stories complete

---

## Phase 10: Polish and Cross-Cutting Concerns

- [ ] T044 [P] Create `docs/auth.md`: how sign-in works for users, how an admin manages the allowlist and approval queue (with `curl` examples against the contract, including the `Origin` header), how to create the first admin, how to read the capture directory in development (`docker compose exec api ls /var/mail-capture`), the Google-only production setup (`MAIL_BACKEND=disabled`), the going-live checklist from `contracts/operator-cli.md`, and troubleshooting (code not arriving, locked out, cookie not set over HTTP)
- [ ] T045 [P] Update `README.md`: add the `api` service, `make test-api`, and a link to `docs/auth.md` and `specs/002-passwordless-auth/`
- [ ] T046 [P] Update `.github/workflows/ci.yml`: after `make test` also run `make test-api ARGS="-m 'not slow'"` (generated secrets only; the `api_external` template stays empty), and print `docker compose logs api` on failure
- [ ] T047 Run everything: `make down && make up`, `make test`, `make test-api`, `make scan-secrets`; run every scenario in `specs/002-passwordless-auth/quickstart.md` that does not need SMTP, Google or production mode; add a "Validation results" section at the end of `specs/002-passwordless-auth/spec.md` ticking SC-001 to SC-009 with evidence and listing what is still blocked on `[MANUAL]` tasks
- [ ] T048 Confirm `git status` shows no `.env` or `secrets/` file staged, run `make scan-secrets`, commit and push

---

## Phase 11: Operator tasks (need you; do not block the code)

- [ ] T049 [MANUAL] (optional, deferred by the operator on 2026-11-15: production starts Google-only with `MAIL_BACKEND=disabled`) When an email service is wanted later: choose an SMTP provider, create a sender address and publish the DNS records it asks for (SPF and DKIM at least), put `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` into `secrets/api_external`, set `MAIL_BACKEND=smtp`, and confirm a test code arrives
- [ ] T050 [MANUAL] The Google OAuth client exists and its id and secret are in `secrets/api_external` (done by the operator on 2026-11-15). Remaining: check the redirect URI is exactly `https://vcp.onepercentdata.site/v1/auth/google/callback`, sign in with a real Google account that is on the allowlist, and check that an account that is not allowlisted becomes pending and an unverified one is refused
- [ ] T051 [MANUAL] Go live (Google only): set `APP_ENV=production` and `MAIL_BACKEND=disabled` in `.env`, `make up-prod`, check `https://vcp.onepercentdata.site/v1/health` reports `mail_backend: disabled`, create the first admin with the address of the Google account you will use, open `https://vcp.onepercentdata.site/v1/auth/google/start` in a browser, sign in, confirm in the browser tools that the cookie is `Secure; HttpOnly; SameSite=Lax`, then add the first real users through the admin API (copy the session cookie from the browser tools into `curl` until the admin screens exist)

---

## Dependencies and Execution Order

### Phase dependencies

- Phase 1 (Setup) → Phase 2 (Foundational) → all user stories
- **US1** needs only Phase 2 (sessions, dependencies, the sign-in service)
- **US2** needs US1 (it uses sessions and `require_role`)
- **US3** needs US1 (it completes the gate in the same service function)
- **US4** needs US1 and US3 (it reuses the gate including the pending path)
- **US5** needs US1 (it wires limits into the existing routes)
- **US6** needs only Phase 2 (it works on `users` and the audit writer)
- **US7** needs the audit writes of US1 to US6, so it comes last
- Polish needs all desired stories; Phase 11 is operator work and can run in parallel with code once US1 exists (T049) or after US4 (T050)

### Story completion order

`US1 → US2 → US3 → (US4, US5 in parallel) → US6 anytime after Phase 2 → US7 → Polish`

### Within each story

Tests first (written and failing), then implementation, then the "run and commit" task.

### Parallel opportunities

- Setup: T003, T005, T006
- Foundational: T011 with T010; T013, T014, T015, T016 are independent modules and can run together
- After US1: US2, US3 and US5 touch different parts and can be worked on by separate sessions if `api/app/auth/service.py` and `api/app/auth/routes.py` edits are merged sequentially
- US6 can start right after Phase 2
- Polish: T044, T045, T046

### Parallel example: Foundational modules

```text
T013 api/app/security.py        + api/tests/unit/test_security.py
T014 api/app/audit.py           + api/tests/unit/test_audit_scrub.py
T015 api/app/rate_limit.py      + api/tests/unit/test_rate_limit.py
T016 api/app/mailer.py          + api/tests/unit/test_mailer.py
```

## Implementation Strategy

1. **MVP**: Phase 1, Phase 2, US1. Result: an allowlisted person signs in with a code and reaches a protected endpoint. Stop and validate with `make test-api ARGS="-k signin"`.
2. **Control**: add US2 and US3. This is the minimum for the promise "only approved people" (principle I proven by tests).
3. **Convenience and safety**: US4 (Google) and US5 (limits) in parallel, then US6 so the system can be started by a real operator.
4. **Accountability and polish**: US7, docs, CI, full validation.
5. **Operator tasks** (T049 to T051) are done when you are ready for real email and real users.

**Manual tasks that block "production-ready" but not the code**: T050 (real Google sign-in check) and T051 (go-live). T049 (SMTP) is optional and deferred. Each is flagged `[MANUAL]`.

## Notes

- Commit after each "run and commit" task; push when a phase checkpoint passes.
- Never commit `.env` or anything under `secrets/` except `secrets/README.md`.
- Never log or store a sign-in code, session token or Google token in readable form; tests enforce it.
- Tests that need time use the injected `FakeClock`, never `sleep`.
- Purging old `email_codes` and `sessions` rows is deferred (data-model.md); it is not part of this feature.
