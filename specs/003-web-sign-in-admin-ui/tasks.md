---
description: "Task list for 003-web-sign-in-admin-ui"
---

# Tasks: Web Pages for Sign-in, Access Request and Admin Management

**Input**: Design documents from `/specs/003-web-sign-in-admin-ui/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md; features 001 and 002 running (`make up`), production settings as in `docs/auth.md`

**Tests**: Included and required. Constitution Development Workflow: security-relevant code ships with failure-case tests. Each phase has a Tests block that is the acceptance test for every task in that phase; documentation and `[MANUAL]` tasks are accepted by the check named in the task.

**Organization**: Grouped by user story (US1 to US6 from spec.md). Paths are relative to the repository root.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1 to US6, only in user-story phases
- **[MANUAL]**: needs the operator (browser, Google accounts, production switch)

**How tests run**: `make test-api` (subset: `make test-api ARGS="-k access"`), `make test` (feature 001 suite), `make scan-secrets`. All three stay green after every phase. Pages are plain files in `api/app/web/`; the image is rebuilt by `make up-prod`.

**Operator-approved decisions (must stay visible in the tasks)**: a pending person gets a *waiting session* (amends 002 FR-008); the constitution gets a PATCH amendment (Principle I, v1.0.1); the 002 tests that assert "pending has no session" are strengthened, not deleted; one deny-by-default test walks every route.

---

## Phase 1: Setup

**Purpose**: records first (rules before code), then schema and shared plumbing

- [X] T001 Amend `.specify/memory/constitution.md` to **v1.0.1** (PATCH): in Principle I first change "every path MUST pass the admin allowlist" to "every path **to an approved session** MUST pass the admin allowlist", then add "An unapproved person MAY hold a *waiting session* that only allows reading their own status, submitting an access request and signing out; every other protected action MUST still require an approved (active) person, and removing, rejecting or suspending a person MUST end the waiting session like any other." Update the footer to `**Version**: 1.0.1 | **Ratified**: 2026-11-15 | **Last Amended**: 2026-11-15` and add a one-line "Sync Impact" note listing what was checked. Acceptance: `grep -n "waiting session" .specify/memory/constitution.md` shows the clause and the version line reads 1.0.1.
- [X] T002 Amend `specs/002-passwordless-auth/spec.md`: add a dated section "## Amendments" right after the header (2026-11-15, reason: operator wants waiting people to hold a session so the web UI can show their request status; reference `specs/003-web-sign-in-admin-ui/spec.md` FR-004/FR-006 and constitution v1.0.1) and update FR-008 to "...MUST create the account in a pending state, MUST create only a *waiting session* that grants no protected action (see Amendments), and MUST show the person in an admin approval queue"; update the User Story 3 introduction (line "gets no session"), its Independent Test, the matching acceptance scenarios (US3 scenario 2 and US4 scenario 2 "no session is created" → "only a waiting session is created; every protected action still refuses it") and the edge case "signs in again before being approved: still no session" → "still one waiting session path, still one queue entry". Do not change anything else. Acceptance: `git diff specs/002-passwordless-auth/spec.md` shows only those edits plus the Amendments section.
- [X] T003 Sync check of dependent documents after T001/T002: run `grep -rniE "no session|without a session|MUST NOT create a session" specs docs README.md api/app api/tests db` and fix every remaining statement that is now false (code comments, `specs/002-passwordless-auth/{plan,research,data-model,quickstart}.md`, `contracts/`); record in the T001 "Sync Impact" note what was changed. Acceptance: the grep returns only lines that are about rejected/suspended/refused people or are quoted history inside the Amendments section.
- [X] T004 Create `db/migrations/20261115020000_access_request.sql` (dbmate up/down) altering `app.users`: add `request_reason text` with `CHECK (request_reason IS NULL OR char_length(request_reason) BETWEEN 1 AND 500)`, add `request_submitted_at timestamptz`, and add to the existing `name` column `CHECK (name IS NULL OR char_length(name) BETWEEN 1 AND 80)`; down migration drops the three. No new grants (table-level grants to `api_rw` already cover the columns). Extend `tests/foundation/test_auth_schema.py` and `tests/foundation/test_migrations.py` (if it lists migrations) so: a 501-character reason, an 81-character name and an empty-string name are rejected by the database, 500 and 80 characters are accepted, `api_rw` can update the new columns, and the migration is applied exactly once in `app.schema_migrations`. Acceptance: `make test ARGS="-k auth_schema"` passes.
- [X] T005 [P] Add `api/tests/unit/test_audit_scrub.py::test_access_requested_detail_never_carries_text`: marked `pytest.mark.skipif("access_requested" not in audit.ACTIONS)` (the action itself is added in T019, so the suite stays green; the test starts running when T019 lands); it must accept `audit.record(..., "access_requested", detail={"has_name": True, "has_reason": False})` and reject a `detail` with keys `name` or `reason_text` (add `name` and `reason_text` to `FORBIDDEN_KEYS` in `api/app/audit.py`; this does not change `ACTIONS`). Acceptance: `make test-api ARGS="-k audit_scrub"` is green now and after T019.
- [X] T006 [P] Create `api/app/security_headers.py` with an ASGI/HTTP middleware adding to **every** response: `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; font-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`, `Strict-Transport-Security: max-age=31536000` only when `settings.app_env == "production"`, and `Cache-Control: no-store` for HTML and `/v1/*` responses (static assets: `no-cache`); register it in `api/app/main.py` **after** the `origin_guard` middleware so it is the outermost one (middleware added last wraps the others) and the guard's 403 also carries the headers. Write `api/tests/unit/test_security_headers.py` (a tiny FastAPI app with the middleware: all headers present on a 200, on a 404 and on a JSON error; HSTS only in production; exact CSP string equality; no `unsafe-inline` or `unsafe-eval` and no `http:`/`https:` source in the CSP). Acceptance: `make test-api ARGS="-k security_headers"`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the waiting session and its deny-by-default proof. No user story starts before this phase is complete.

**⚠️ CRITICAL**: this phase changes who may hold a session; the route-walk test must be green before any page work.

- [X] T007 Add `current_person` to `api/app/auth/deps.py` next to `current_user`: same cookie lookup, same revoked/expiry checks, but accepts `u.status IN ('active','pending')` and returns `CurrentPerson(id, email, role, status, session_id)` (a new frozen dataclass; `role` is meaningful only when active). `current_user` and `require_role` MUST stay byte-for-byte unchanged in behaviour (still `u.status = 'active'`). Add a comment on `current_user` stating that it must not be relaxed. Write `api/tests/integration/test_deps_status.py` (integration, because it needs the database fixtures) with a direct-lookup test using the `sql` fixture: active person accepted by both, pending accepted only by `current_person`, suspended/rejected accepted by neither, revoked and expired sessions rejected by both.
- [X] T008 Change `_gate` in `api/app/auth/service.py` so a person who ends `pending` (new unknown email with no entry, or existing pending person with no matching entry) gets a waiting session: call `create_session(...)` inside the same transaction, record `session_created` after the existing `signin_pending`, and return `SignInOutcome(status="pending", token=token)`. Refused results (suspended, rejected) still create nothing. Make sure code verification (`verify_code`) and the Google callback both set the cookie when `outcome.token` is present: update `api/app/auth/routes.py` so the Google callback calls `set_session_cookie` for `pending` as well as `signed_in`, redirecting to `/?signin=pending`; `verify_code` already sets the cookie from `outcome.token`. The request-code response stays untouched (uniform response rule).
- [X] T009 First add the fixture `login_pending(email)` to `api/tests/integration/conftest.py` (requests a code and verifies it for an email with **no** allowlist entry, asserts the response is `{"status":"pending"}`, and returns the client holding the waiting-session cookie); it is used by T010, T013 and T018. Then strengthen the three 002 tests that asserted "pending has no session" (do not delete their other assertions):
  - `api/tests/integration/test_unapproved.py`: rename `..._becomes_pending_without_a_session` to `..._becomes_pending_with_only_a_waiting_session`; assert a cookie is set, exactly one `sessions` row exists for that person, `/v1/me` is 401, and `GET /v1/admin/users` is 401; in `test_signing_in_again_while_pending_adds_no_second_entry` assert still one `users` row and the response now has a session cookie each time; in `test_adding_a_matching_entry_lets_a_pending_person_in_at_the_next_sign_in` assert the earlier waiting session starts working for `/v1/me` as soon as the person becomes active.
  - `api/tests/integration/test_google.py`: `test_a_verified_unknown_email_becomes_pending_without_a_session` → `..._with_a_waiting_session`: assert `__Host-session` IS in cookies, `/v1/me` is still 401, `users.status = 'pending'`; the suspended-person and unverified-email tests keep asserting no session.
  - `api/tests/integration/test_audit.py`: `test_google_signin_pending_and_refused` and `test_the_api_role_cannot_change...` unaffected; add to the secrets test that the waiting session token is not stored or logged readable (add `pending_token` to the `secrets` dict); add an assertion that a `session_created` event exists for a pending sign-in.
  Also assert in `test_unapproved.py` that the waiting-session cookie's `Set-Cookie` has `HttpOnly`, `Secure`, `SameSite=lax` and `Path=/` (FR-017), and that repeated pending sign-ins are subject to the existing 30-verifies-per-10-minutes cap per address (the 31st returns 429; this is the accepted bound on waiting-session growth, research R-3). Acceptance: `make test-api ARGS="-k 'unapproved or google or audit'"` passes.
- [X] T010 Write `api/tests/integration/test_deny_by_default.py`: using `app.routes` (from the `app` fixture) enumerate every registered `APIRoute` under `/v1` plus the page routes added later (the test re-reads `app.routes` at run time, so routes added in later phases are covered automatically). Maintain an explicit allow-list `WAITING_ALLOWED = {("GET", "/v1/access"), ("POST", "/v1/access/request"), ("POST", "/v1/auth/logout"), ("GET", "/v1/health")}` plus the public sign-in routes (`/v1/auth/code/request`, `/v1/auth/code/verify`, `/v1/auth/google/start`, `/v1/auth/google/callback`) and page routes marked public. For every other route, fill path parameters with a random UUID, send `{}` as JSON body for non-GET routes together with the `Origin` header (dependencies run before body validation, so a refusal is 401/403 not 422), call it with a **waiting session** (from `login_pending`) (pending person signed in through the gate) and with no session, and assert both get `401` (or `403` for page routes that refuse non-admins, status checked per route kind) with identical JSON body; then call it with an active non-admin session and assert admin routes give `403`. The test fails loudly (names the route) if a route exists that is neither in the allow-list nor refuses the waiting session, so a future route cannot be added unprotected. Acceptance: `make test-api ARGS="-k deny_by_default"` passes now (before `/v1/access` exists the allow-list entries are skipped when the route is absent; once US1/US2 land the test asserts they exist).
- [X] T011 [P] Create the web package skeleton: `api/app/pages.py` with an `APIRouter` and helper `serve(path)` returning `FileResponse` from `api/app/web/`, a handler for unmatched non-`/v1` `GET` paths returning `web/404.html` with status 404 (the `/v1/*` JSON error shape unchanged, HEAD/other methods keep the JSON `method_not_allowed`), static mount for `GET /static/*` from `api/app/web/static/` with path-traversal protection (reject `..`, absolute paths, dotfiles), and registration in `api/app/main.py`; create `api/app/web/404.html` and `api/app/web/403.html` (plain "Page not found" / "You do not have access to this page" with a link to `/`, no script); update `api/Dockerfile` only if needed so `app/web` is copied (it is inside `app/`, so the existing `COPY app ./app` already covers it; add `api/tests/integration/test_pages.py::test_pages_are_in_the_image` that checks `Path("app/web/index.html")` exists in the test container). Write `api/tests/integration/test_pages.py` first tests: unknown path → 404 HTML with the full header set; `/v1/nothing` → JSON 404 unchanged; `/static/../../etc/passwd`, `/static/%2e%2e/app/config.py` and `/static/.env` → 404; `POST /` without Origin → 403 JSON from the origin guard (unchanged body) **with the full header set**. Acceptance: `make test-api ARGS="-k pages"`.
- [X] T012 Update `caddy/Caddyfile`: replace the placeholder catch-all `handle { respond "AI Agent Platform is up." 200 }` with `handle { reverse_proxy api:8000 }`; keep `/v1/*` and `/healthz` handling and the internal `:8081` block; update the header comment; mention the catch-all in `docs/vps-setup.md`. **Only the file is changed here; the live Caddy is recreated in T042** (after the new `api` image is running, so `/` is never proxied to an image without pages). Acceptance: `docker compose run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile` succeeds (the config parses) and `make test` stays green.

**Checkpoint**: waiting session exists and is provably useless for everything except status, request and sign-out.

---

## Phase 3: User Story 1 - Sign in and reach the inside area (Priority: P1) 🎯 MVP

**Goal**: signed-out visitors see the Google button; approved people see the inside placeholder; sign-out works.

**Independent Test**: with one approved person, open `/` signed out, sign in with Google, see email and role, sign out.

### Tests for User Story 1

- [X] T013 [US1] Write `api/tests/integration/test_access_status.py` for `GET /v1/access` (contracts/http-api.md): active person → 200 `{"email","status":"active","role","submitted":false,"name":null,"reason":null}`; pending person with a waiting session → 200 with `status:"pending"`, `role:null`; no cookie, forged cookie, expired session (FakeClock `advance`), revoked session, suspended person and rejected person → identical `401 {"error":"unauthenticated"}`; after an admin approves a pending person the same cookie returns `status:"active"` with the chosen role, with no new sign-in; the response never contains the session token. Add to `api/tests/integration/test_pages.py`: `GET /` returns 200 `text/html`, contains a link to `/v1/auth/google/start`, contains no `<script>` without `src`, and loads only `/static/*` URLs; `GET /static/app.js`, `/static/common.js`, `/static/style.css`, `/static/favicon.svg` return correct content types and the header set.

### Implementation for User Story 1

- [X] T014 [US1] Create `api/app/auth/access.py` with `router = APIRouter(prefix="/v1")` and `GET /access` using `current_person`; returns the shape from contracts/http-api.md (`role` only when status is `active`; `name`, `reason`, `submitted` read from `app.users`); include the router in `api/app/main.py`. Acceptance: T013 API tests.
- [X] T015 [P] [US1] Create `api/app/web/static/style.css`: CSS variables for colours with a `@media (prefers-color-scheme: dark)` override, system font stack (no web fonts), single-column layout with `max-width: 40rem` that works at 320 px with no horizontal scroll, visible `:focus-visible` outline on every interactive element, large tap targets (min 2.5rem), classes `.card`, `.btn`, `.btn-secondary`, `.field`, `.status`, `.table` (becomes stacked cards under 700 px), `[hidden] {display:none !important}`. No `@import`, no `url(http…)`, no `expression`. Create `api/app/web/static/favicon.svg` (a plain inline-free SVG, no script).
- [X] T016 [P] [US1] Create `api/app/web/static/common.js` (ES module) exporting: `api(method, path, body)` (same-origin `fetch`, `credentials: "same-origin"`, JSON in/out, returns `{status, data}`, never throws on HTTP errors; unsafe methods rely on the browser adding `Origin`), `el(tag, props, ...children)` (creates elements; children strings become **text nodes**; only a whitelist of attributes: `class`, `type`, `value`, `href`, `id`, `for`, `name`, `disabled`, `hidden`, `maxlength`, `rows`, `autocomplete`, `aria-*`, `role`, `data-*`; event handlers passed as functions via `addEventListener`), `show(viewId)`, `setStatus(node, kind, text)`, `fmtTime(iso)` (local time text), `messageFor(status, data)` implementing the error mapping in contracts/pages.md (401/403/`last_admin`/409/422/other) with the fixed generic text for anything else. It MUST NOT use `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval` or `new Function`.
- [X] T017 [US1] Create `api/app/web/index.html` (one page, `lang="en"`, viewport meta, `<link rel="stylesheet" href="/static/style.css">`, `<script type="module" src="/static/app.js">`, no inline script, style or event attributes) with hidden sections: `#view-loading`, `#view-signin` (heading, short sentence, `<a class="btn" href="/v1/auth/google/start">Sign in with Google</a>`, and a hidden `#code-form` with email field, "Send code" button, code field and "Sign in" button for the email-code path), `#view-inside` (email, role, `<a id="admin-link" hidden href="/admin">Admin area</a>`, "Sign out" button), `#view-request` (filled in US2), `#view-message`. Create `api/app/web/static/app.js` (ES module importing `common.js`): on load read `?signin=` then remove it with `history.replaceState`; call `GET /v1/access`; 200 active → fill `#view-inside` with `textContent` and show the admin link only when role is `admin`; 200 pending → show `#view-request` (US2); 401 → if `signin=refused|error` show `#view-message` with the fixed texts from contracts/pages.md, else show `#view-signin`; call `GET /v1/health` and unhide `#code-form` only when `mail_backend` is present and not `disabled`; code form posts to `/v1/auth/code/request` and `/v1/auth/code/verify` and then reloads the view; "Sign out" posts `/v1/auth/logout` then shows `#view-signin`. All unexpected failures show "Something went wrong. Please try again." Route `GET /` in `api/app/pages.py` to `index.html`.

**Checkpoint**: an approved person can sign in and out in a browser; MVP.

---

## Phase 4: User Story 2 - Request access while waiting for approval (Priority: P1)

**Goal**: waiting people submit name and reason once and see their status.

**Independent Test**: sign in with an unlisted Google account, submit name and reason, see "request sent"; an admin sees both texts.

### Tests for User Story 2

- [X] T018 [US2] Write `api/tests/integration/test_access_request.py`: waiting person `POST /v1/access/request {"name":"Ann","reason":"Sales team"}` → 201 `{"submitted":true}`, DB has `name`, `request_reason`, `request_submitted_at` set and exactly one `access_requested` audit event whose `detail` is `{"has_name": true, "has_reason": true}` and contains neither text; second POST with different text → 200 `{"submitted":false,"already_submitted":true}` and stored text unchanged; empty body fields (`null`, `""`, whitespace) count as a submission with NULL columns and the form is not offered again; 81-character name, 501-character reason, extra field and wrong types → 422; text with control characters and newlines is stored cleaned (control characters removed, whitespace runs collapsed to single spaces, trimmed; `"<b>x</b><script>"` stored byte-for-byte as typed, not altered); no cookie / forged cookie / expired / revoked → 401 and nothing stored; an **active** person POST → 409 `not_pending`; a rejected person's former session → 401; request without the Origin header → 403; `GET /v1/access` afterwards returns `submitted:true` and the stored text; also: two simultaneous POSTs (asyncio.gather) result in exactly one stored submission and one audit event.

### Implementation for User Story 2

- [X] T019 [US2] Implement `submit_request(state, person, name, reason, ip)` in `api/app/auth/service.py`: clean text (strip, remove Unicode control characters, replace runs of whitespace including newlines with one space, empty → NULL); one transaction: `UPDATE app.users SET name = %s, request_reason = %s, request_submitted_at = %s, updated_at = %s WHERE id = %s AND status = 'pending' AND request_submitted_at IS NULL RETURNING id`; zero rows → look up the person: still pending and already submitted → `already_submitted`, otherwise (active) → 409 `not_pending` via a new `ApiError` in `api/app/errors.py`; on success `audit.record(... "access_requested", target=email, ip=ip, detail={"has_name": bool, "has_reason": bool})`. Add `POST /access/request` to `api/app/auth/access.py` with a Pydantic body `AccessRequest(name: str | None = Field(None, max_length=80), reason: str | None = Field(None, max_length=500))` (`extra="forbid"`) using `current_person`; limits are checked **after** cleaning as well (post-clean length must still satisfy the DB checks). Add `"access_requested"` to `ACTIONS` in `api/app/audit.py` **in this task** (not earlier, so the suite stays green), add it to the `covered` set and add a scenario test to `api/tests/integration/test_audit.py` (the `only(sql, "access_requested")` row has actor_email, ip). Acceptance: T018.
- [X] T020 [US2] Fill `#view-request` in `api/app/web/index.html` and `app.js`: text "Your access request is waiting for an administrator. Nothing else is available until you are approved."; when `submitted` is false show a form (labels "Your name (optional)", "Why do you need access? (optional)", `maxlength` 80 and 500, a note with the limits, "Send request" button); after success or `already_submitted` show the stored name/reason read-only (set via `textContent`) and "Your request has been sent."; "Check again" button re-calls `GET /v1/access` and shows the inside view when the status is `active`; "Sign out" button; 401 during submit → "You were signed out. Please sign in again." with a link to `/`. Acceptance: T018 plus quickstart step 3.
- [X] T021 [US2] Extend `api/tests/integration/test_deny_by_default.py` so the allow-list entries for `/v1/access` and `/v1/access/request` are now mandatory (the test fails if either route is missing) and add the assertion that a waiting session can call exactly these plus logout and nothing else.

**Checkpoint**: the whole person-side flow works; waiting people are still locked out of everything else.

---

## Phase 5: User Story 3 - Approve or reject people from the admin area (Priority: P1)

**Goal**: admins open `/admin` and approve or reject people from the queue with confirmations.

**Independent Test**: with one pending person with text, approve as `user` (reload → inside area with role user); reject a second person.

### Tests for User Story 3

- [X] T022 [US3] Write `api/tests/integration/test_admin_pages.py`: `GET /admin` with no session → 302 to `/` (no body data, headers present); with a waiting session → 403 HTML (`403.html`); with an active `user` or `builder` → 403 HTML; with an active admin → 200 `admin.html`; the same matrix for `GET /admin/assets/admin.js` (and 404 for unknown asset names and traversal attempts like `/admin/assets/../../config.py`); neither response contains any person data or secret (assert the response body does not contain any email present in the database); an admin whose session is revoked after the page loaded gets 401 from the next API call. Write `api/tests/integration/test_admin_ui_api.py` first tests: `GET /v1/admin/users` items now include `name`, `request_reason`, `request_submitted_at` (null when absent) and keep all previous fields; queue ordering is by `created_at`; approve/reject on an already-handled person: approving an `active` person with a role leaves them active with the new role, and and the new optional query parameter `expect_status` (see contracts/http-api.md): `POST .../approve?expect_status=pending` and `.../reject?expect_status=pending` on a person who is no longer pending → `409 {"error":"already_handled"}`, nothing changed and no audit event for the refused attempt; with the parameter matching, behaviour is exactly as before; without the parameter behaviour is unchanged (existing `test_admin.py` untouched); an unknown value (`expect_status=bogus`) → 422; two concurrent approve calls with `expect_status=pending` → exactly one 200 and one 409.

### Implementation for User Story 3

- [X] T023 [US3] Add the three fields to `_user()` and the `list_users` SELECT in `api/app/admin/service.py` (`name`, `request_reason`, `request_submitted_at` via `_iso`); add the optional `expect_status: Literal["pending","active","suspended","rejected"] | None = Query(None)` parameter to the approve, reject, suspend and role routes in `api/app/admin/routes.py`, pass it to `_change_user`, which — after the `FOR UPDATE` read and before any change — raises `ApiError(409, "already_handled", "Someone else already handled this person. Refresh the list.")` when it is given and differs from the person's current status; no other admin behaviour changes. Acceptance: T022 API tests and the existing `test_admin.py` still pass unchanged.
- [X] T024 [US3] Implement admin gating in `api/app/pages.py`: `GET /admin` and `GET /admin/assets/{name}` use a dependency built on `current_person` (do not duplicate the session lookup): no valid session → `RedirectResponse("/", 302)`; person not active or role not admin → `403.html` with status 403; admin → file. Asset names are validated against a fixed allow-list (`admin.js`) and file reads are confined to `api/app/web/admin-assets/`. Register in `api/app/main.py`. Add the routes to the route walk in `test_deny_by_default.py` (page routes kind "page": waiting session and no session never get 200).
- [X] T025 [P] [US3] Create `api/app/web/admin.html` (shell: header with "Admin" title, link back to `/`, tab links `#queue`, `#allowlist`, `#people`, `#audit`, a `<main id="panel">`, a status line `<p id="status" role="status">`, one shared `<dialog id="confirm">` with title, message, optional role `<select>`, "Confirm"/"Cancel" buttons; `<script type="module" src="/admin/assets/admin.js">`; no inline script/style/handlers) and `api/app/web/admin-assets/admin.js` skeleton: imports `/static/common.js`, hash router, `confirmDialog({title, message, roleChoice})` returning a Promise (native `<dialog>.showModal()`, Escape cancels, focus returns to the trigger), `renderTable(columns, rows)` using `el()`/`textContent` only, loading and error states via `messageFor`; then the **Queue** tab: `GET /v1/admin/users?status=pending`, columns email, name, reason, requested at (`fmtTime`), a role select (default `user`) with "Approve" and a "Reject" button, each through `confirmDialog`; "Nothing is waiting." when empty; send `expect_status=pending` with every approve and reject; on `409 already_handled` show "Someone else already handled this person" and refresh; after each action refresh the list. Acceptance: T022 and quickstart steps 3 to 5.

**Checkpoint**: approval works from the browser.

---

## Phase 6: User Story 4 - Manage the allowlist and people (Priority: P2)

**Goal**: admins manage allowlist entries and people from pages.

**Independent Test**: add an email, sign in as them, remove the entry, see their next request fail; change a role; suspend; reactivate.

### Tests for User Story 4

- [X] T026 [US4] Extend `api/tests/integration/test_admin_ui_api.py`: `GET /v1/admin/allowlist` items include `added_by_email` (the admin's email; null when the adder row is gone) and keep all previous fields; reactivation through the existing approve action: a person suspended by an admin and a rejected person become `active` with the chosen role, `suspended_reason` cleared, audit action `user_approved`, and their old sessions stay revoked (they must sign in again); `expect_status` works on the suspend and role routes the same way (stale status → 409 `already_handled`); last-admin protection is still reported as `409 last_admin` for suspend, role change and allowlist removal of the only admin (error code/message asserted because the page maps it); malformed email and domain → 422 with the server's message; duplicate entry → 409.

### Implementation for User Story 4

- [X] T027 [US4] In `api/app/admin/service.py` change `list_entries` to `LEFT JOIN app.users u ON u.id = a.added_by` and return `added_by_email` (null if none) in `_entry`; keep ordering. Acceptance: T026 allowlist assertions and existing `test_admin.py`.
- [X] T028 [US4] Add the **Allowlist** tab to `api/app/web/admin-assets/admin.js`: table (kind, value, default role, added by, added at), an add form (kind select email/domain, value input, default role select, "Add" button; inline hint for domain format), and a "Remove" button per row using `confirmDialog` with the message "Removing this entry signs out people who were allowed only by it, immediately."; show server 422/409 messages via `messageFor`; refresh after each action.
- [X] T029 [US4] Add the **People** tab to `admin.js`: `GET /v1/admin/users`, table (email, name, status, role, last sign-in), a role select with "Change role" (confirm), "Suspend" (confirm, shown for active people), and "Reactivate" for suspended and rejected people (a role select plus confirm; calls the approve endpoint; every People-tab action sends `expect_status=<the status shown in the row>` and maps `409 already_handled` to a refresh with the same message; note "This signs them in again only when they sign in."); the `last_admin` error shows "This would remove the last active admin. Make another admin first." and leaves the table unchanged. Acceptance: T026 and quickstart step 7.

---

## Phase 7: User Story 5 - Read the audit log (Priority: P2)

**Goal**: admins read, page and filter the audit trail in the browser.

**Independent Test**: after a few actions, filter by action and by person, then page to older events.

### Tests for User Story 5

- [ ] T030 [US5] Extend `api/tests/integration/test_audit.py` or `test_admin_ui_api.py`: `GET /v1/admin/audit` response is now `{"items": [...], "actions": [...]}` where `actions` equals `sorted(audit.ACTIONS)`; `person=alice` matches events whose `actor_email` or `target` start with `alice` case-insensitively and nothing else; `person` containing `%`, `_` or `\` matches literally (seed a row with `a_b@example.com` and one with `axb@example.com`, `person=a_b` returns only the first); `person` over 254 characters → 422; `person` combined with `action` and `before` pages correctly with no repeats or gaps (seed 12 rows, page 5+5+2); all earlier assertions about `items` (fields, ordering, limit bounds, roles, 401/403) still pass unchanged.

### Implementation for User Story 5

- [ ] T031 [US5] Update `read_audit` in `api/app/admin/service.py` and the route in `api/app/admin/routes.py`: add `person: str | None = Query(None, max_length=254)`; build `lower(actor_email) LIKE %s ESCAPE '\' OR lower(target) LIKE %s ESCAPE '\'` with the value lower-cased, `\`, `%` and `_` escaped and `%` appended (bound parameters only); return `{"items": [...], "actions": sorted(audit.ACTIONS)}` from the route. Acceptance: T030.
- [ ] T032 [US5] Add the **Audit** tab to `admin.js`: filter row (action `<select>` filled from the response's `actions` plus "All actions"; person text input; "Apply" button), table (time, actor, action, target, outcome, IP, details as `JSON.stringify` text), 50 rows per page using `limit=50`, an "Older" button that sends `before=<last id>` and appends rows, "Nothing matched." when empty, "No more events." when a page returns fewer than 50. Acceptance: T030 and quickstart step 8.

---

## Phase 8: User Story 6 - Everything stays locked to the right people (Priority: P1)

**Goal**: prove the safety rules hold across all pages, including future edits.

**Independent Test**: lint passes; a malicious name is shown literally; headers present everywhere.

### Tests for User Story 6

- [ ] T033 [US6] Write `api/tests/unit/test_page_sources.py` (reads every file under `api/app/web/`): HTML files contain no `<script>` without a `src` attribute, no attribute starting with `on`, no `style=` attribute, no `<style>` block, no `javascript:` URL, no `http://` or `https://` URL anywhere except inside visible text of `404.html`/`403.html` (none expected), every `<script>` has `type="module"` and a `src` starting with `/static/` or `/admin/assets/`, every `<link>` and `<img>` points to `/static/…`; JS files contain none of `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval(`, `new Function`, `setAttribute("style"`, `.style.cssText`, `localStorage`/`sessionStorage`/`document.cookie` (no secrets or tokens client-side), and no `http://`/`https://` literal; CSS contains no `@import` and no `url(` with a scheme or `//`; the test prints the file and line on failure and has a self-test feeding it a deliberately bad snippet to prove it catches each rule. Also asserts every file is under 60 KB (plan performance target).
- [ ] T034 [US6] Extend `api/tests/integration/test_pages.py`: iterate over a list of public paths (`/`, `/static/app.js`, `/static/style.css`, `/nope`, `/admin` signed out, `/admin` as non-admin) and assert the exact CSP header, `nosniff`, `no-referrer` on each; HTML responses are `no-store`; no response sets a cookie except the sign-in and logout routes; the CSP of `/` contains no `unsafe-inline`.
- [ ] T035 [US6] Write `api/tests/integration/test_stored_text_is_inert.py`: a waiting person submits name `"<img src=x onerror=alert(1)>"` and reason `"</td><script>alert(1)</script>"`; an admin fetches `/v1/admin/users` and gets them back as JSON strings unchanged (JSON-encoded, content type `application/json`, `nosniff`); the admin page files do not contain those strings; and a static check that `admin.js` and `app.js` render the fields only through `el()`/`textContent` (grep for the field names `request_reason`, `name`, `email` in each render function and assert no `innerHTML` anywhere, reusing T033's rules). This is the automated half of the XSS acceptance; the browser half is quickstart steps 3 and 4 (done in T043).
- [ ] T036 Add a meta-test to `test_deny_by_default.py`: it collects the set of routes it actually exercised and fails if any registered `/v1` route or page route is missing from that set (no route silently skipped), and if the allow-list names a route that is not registered (stale allow-list).

### Implementation for User Story 6

- [ ] T037 [US6] Fix whatever T033–T036 find in the page files, headers or routes (no behavioural features here); ensure `api/app/web/` contains no inline handler, no style attribute and that dialogs, buttons and inputs have accessible names (`label`, `aria-label` or visible text) and that tab order is logical. Acceptance: `make test-api` fully green.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T038 [P] Update `docs/auth.md`: replace the "Managing people without a screen" curl workflow with "Managing people in the admin area" (what each tab does, how a new person requests access, that a waiting person holds a limited sign-in and a reload takes them inside after approval), keep a short "Break-glass: curl" appendix for when the page is down, add a troubleshooting table (CSP console errors, "403 on /admin", "signin=refused", blank page after deploy = rebuild with `make up-prod`), and document the waiting-session rule and the constitution v1.0.1 clause.
- [ ] T039 [P] Update `README.md` (what exists now: pages at the site root and `/admin`, link to `docs/auth.md`), `docs/phase0-verification.md` only if it lists auth manual steps, and `specs/003-web-sign-in-admin-ui/spec.md` with a "Validation results" section after the final run (command outputs summarised, counts of tests).
- [ ] T040 Run the full verification: `make test`, `make test-api`, `make scan-secrets`; confirm `make test-api` count increased and nothing in the live database changed (`SELECT count(*) FROM app.users` unchanged by tests). Record the numbers in the spec's Validation results.
- [ ] T041 Commit in small commits (constitution: small focused commits), grouped as: (1) T001–T003 docs/amendments, (2) schema + audit + headers, (3) waiting session + 002 test changes + route walk, (4) pages and US1/US2, (5) admin pages US3–US5, (6) lint/safety tests and docs; each with the required `Co-Authored-By` trailer, **on the branch `dev_spec_03` (created by the operator; never commit this feature to `main`)**, committing after each phase checkpoint rather than only at the end; after T040 passes run `git push -u origin dev_spec_03`. Merging into `main` is the operator's decision (pull request).

---

## Phase 10: Operator validation and go-live [MANUAL]

- [ ] T042 [MANUAL] Deploy the branch you want to run: `make up-prod` (rebuilds the `api` image), then recreate Caddy so it re-reads the changed Caddyfile: `docker compose -f compose.yaml -f compose.backup.yaml --profile prod up -d --force-recreate --no-deps caddy`, then `curl -sI https://vcp.onepercentdata.site/` shows `200`, the CSP header and `content-type: text/html`; `curl -s https://vcp.onepercentdata.site/v1/health` still shows `"mail_backend":"disabled"`.
- [ ] T043 [MANUAL] Walk through `quickstart.md` steps 1–10 with two Google accounts (admin and an unlisted one) and tick each step; also measure: (a) SC-001: from opening the site to a submitted request takes under 2 minutes; (b) SC-002: approving from the admin area home takes at most 3 clicks (Queue tab → Approve → Confirm); (c) SC-006: DevTools → Network shows each page (document plus its script and style) loaded in under 2 seconds; (d) US1 scenario 2 cannot be tried in production because mail is disabled, so confirm only that the code form is **absent**; (e) phone width and keyboard-only operation (step 10). Report any failing step to the coding agent with the screen text.
- [ ] T044 [MANUAL] Decide on the leftovers and close the feature: confirm the age private key at `~/age_identity.txt` has been copied to a password manager and shredded (`shred -u ~/age_identity.txt`); tell the agent which feature comes next. Acceptance: operator confirmation.

---

## Dependencies & Execution Order

- **Phase 1** (T001–T006): T001–T003 first (rules before code); T004, T005, T006 are independent of each other.
- **Phase 2** (T007–T012): T007 → T008 → T009 → T010 (T010 needs the gate change to be meaningful); T011 and T012 are independent of T007–T010 but T012's verification needs T006 and T011 deployed. **Blocks all stories.**
- **US1** (T013–T017) after Phase 2. **US2** (T018–T021) after US1 (needs `access.py` and `index.html`). **US3** (T022–T025) after Phase 2 and T014 (shares `current_person`); can run in parallel with US2 by different people, sequentially here. **US4** after US3 (same `admin.js` and fixtures). **US5** after US3. **US6** after US1–US5 (lints the finished files). **Polish** after US6. **Operator validation** after Polish.
- Within each story: tests first, then service/routes, then pages.

## Parallel Opportunities

- Phase 1: T004, T005, T006 together.
- Phase 2: T011 and T012 alongside T007–T010.
- US1: T015 and T016 together; US3: T025 with T023/T024; US4–US5: T027 and T031 (different functions in `admin/service.py`; run sequentially if editing the same file) with the front-end tab tasks.
- Phase 9: T038 and T039 together.

## Implementation Strategy

- **MVP** = Phases 1–2 + US1 (T001–T017): the site has a front door and approved people can sign in; the waiting-session change is already proven safe by the route walk.
- **Increment 2**: US2 (people can ask for access) then US3 (admins can approve): this is the operator's core request, the `curl` workflow is no longer needed after T025.
- **Increment 3**: US4 and US5 (allowlist, people, audit), then US6 (lock the safety rules in with tests), then Polish and the manual go-live.
- Stop after any phase if the checkpoint tests are green; each phase leaves `main` deployable.
