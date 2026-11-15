# Research: Web Pages for Sign-in, Access Request and Admin Management

No NEEDS CLARIFICATION remained in the spec. Decisions below.

## R-1: Who serves the pages
**Decision**: the existing `api` container serves them (FastAPI file responses); Caddy's catch-all proxies to it.
**Rationale**: the admin gate must be server-side (the HTML/JS of `/admin` is withheld from non-admins), headers must be testable in the existing isolated test run, and one image means one deployment. Caddy-only static serving would put headers and gating in a place the test stack does not run.
**Alternatives**: Caddy `file_server` (rejected: untested config, cannot gate by session); Next.js (rejected: see Complexity Tracking in the plan).

## R-2: Front-end technique
**Decision**: hand-written HTML, CSS and ES modules, no framework, no build, DOM built with `createElement`/`textContent`; native `<dialog>` for confirmations; classes and the `hidden` attribute for state (no inline styles).
**Rationale**: smallest surface, satisfies CSP without nonces, trivially auditable for XSS (a lint test forbids `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, `new Function`, inline `on*=` handlers and `style=`).
**Alternatives**: lit/alpine/htmx (rejected: dependency plus CSP caveats).

## R-3: Who a pending person is, and what they may do (waiting session)
**Decision** (operator's requirement): a person whose sign-in ends `pending` receives a normal session (same cookie, token hash, seven-day expiry) created by the gate in `complete_sign_in`, for both Google and email-code paths. A second dependency `current_person` accepts a session whose person is `active` OR `pending`; it is used only by `GET /v1/access` and `POST /v1/access/request` (and logout already works by cookie). `current_user` and `require_role` are unchanged and still demand `status = 'active'`, so every other route refuses a waiting session exactly as before. The request is written with `UPDATE ... WHERE id = <session person> AND status = 'pending' AND request_submitted_at IS NULL`, so "once" is enforced by the database.
**Rationale**: the person is identified by an ordinary, already-reviewed session instead of a bespoke proof; approval takes effect on reload (no second sign-in); rejection, suspension and allowlist removal already revoke sessions immediately. Default-deny is preserved because the new acceptance rule is opt-in per route and a test walks every registered route.
**Residual risk (accepted after the analysis of 2026-11-15; tested in T009)**: anyone with a Google account (or a mailbox, if codes are enabled) can create a pending person and a session row. Bounded by the existing per-address verify limit, the unchanged one-pending-row-per-email rule, and seven-day expiry; each extra sign-in adds one session row.
**Alternatives**: signed pending cookie (my first proposal, rejected by the operator and more bespoke); a separate "limited" session type or table (rejected: second mechanism to review); allowing `current_user` to accept pending (rejected: would silently open every protected route).

## R-4: After approval
**Decision**: nothing special. The waiting session becomes a full session the moment `status` becomes `active` (status is re-read on every request); the page's next load shows the inside area with the chosen role. A waiting person who is rejected or suspended has the session revoked and sees the refusal message at the next action.
**Amendment recorded**: feature 002 FR-008 ("MUST NOT create a session") and its tests/docs change to "creates a waiting session that unlocks nothing"; `specs/002-passwordless-auth/spec.md`, `docs/auth.md` and the affected tests are updated in this feature's tasks.

## R-5: Reactivation
**Decision**: no new backend action. The existing approve action sets any person to active with the chosen role and clears `suspended_reason`, so "Reactivate" in the People tab calls approve with a role chosen by the admin (shown for suspended and rejected people). Its audit action is `user_approved`.
**Rationale**: satisfies FR-010 without touching gate logic.

## R-6: Gating the admin pages and what is public
**Decision**: `GET /admin` and `GET /admin/assets/*` run the normal session dependency: no session cookie or invalid session → `302 /`; role not admin → `403` refusal page; admin → the file. `index.html`, `static/*`, 403/404 pages are public and contain no data or secrets. All data comes from `/v1/*` calls which enforce the role themselves (the gate on `/admin` is defense in depth, not the only control).
**Rationale**: FR-014 with two independent layers.

## R-7: Security headers
**Decision**: one middleware adds to every response: `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; font-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy: same-origin`, `Permissions-Policy` denying camera/microphone/geolocation, `Strict-Transport-Security` when `APP_ENV=production`, and `Cache-Control: no-store` on HTML and API responses (static assets get `no-cache`).
**Rationale**: FR-016; `form-action 'self'` allows the plain link to `/v1/auth/google/start` because it is a navigation, not a form (CSP `navigate-to` is not used). Redirect to Google is a 302 from our own origin and is unaffected by CSP.

## R-8: Showing the email-code form
**Decision**: the page reads the existing public `GET /v1/health` field `mail_backend`; if it is not `disabled` the code form is shown (two calls to existing endpoints).
**Rationale**: spec FR-001 with no new endpoint. Exposing the backend name is already the case today.

## R-9: Audit person filter and action list
**Decision**: `GET /v1/admin/audit` gains `person` (case-insensitive prefix match on `actor_email` or `target`), and its response gains `actions` (the allowed action names) so the filter dropdown needs no hard-coded copy. Paging keeps the existing `before` cursor.
**Rationale**: FR-012 with additive changes only; prefix match uses an escaped `LIKE` with bound parameters.

## R-10: Request text cleaning
**Decision**: trim, remove control characters, collapse whitespace runs (newlines become spaces), empty → NULL; name ≤ 80, reason ≤ 500 characters, enforced in the request model (422 with a plain message) and by database CHECK constraints.
**Rationale**: FR-007; text is still treated as untrusted at render time.

## R-11: 404 and error pages
**Decision**: a handler for unmatched non-`/v1` GET paths returns `404.html`; `/v1/*` keeps the JSON error shape. Unexpected failures in the pages show a fixed generic message and never print server text.
**Rationale**: FR-022 and the edge cases.

## R-12: Two admins acting on the same person
**Decision**: the admin actions approve, reject, suspend and role-change accept an optional query parameter `expect_status`. The page sends the status it displayed; inside the existing transaction, after the row lock, a mismatch raises `409 already_handled` and changes nothing. Omitting the parameter keeps today's behaviour, so feature 002 callers and tests are unaffected.
**Rationale**: spec US3 scenario 5 and FR-011 need the second admin to be told; without a precondition the API silently re-applies the second click. Additive, no migration.
**Alternatives**: a version column or ETag (rejected: schema change for a rarely hit case); client-side re-fetch before acting (rejected: still racy).
