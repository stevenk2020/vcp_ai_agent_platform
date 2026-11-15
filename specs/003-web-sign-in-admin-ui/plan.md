# Implementation Plan: Web Pages for Sign-in, Access Request and Admin Management

**Branch**: `dev_spec_03` | **Date**: 2026-11-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/003-web-sign-in-admin-ui/spec.md`

## Summary

Give feature 002 a front end. The existing `api` service also serves a handful of plain static pages
(sign-in/landing, access request, inside placeholder, admin area, 404) plus tiny vanilla JavaScript files,
all from the same address as `/v1/*`, so there are no cross-origin calls and no build step. Every admin
action in the pages calls the existing admin endpoints; the only server-side additions are:
(1) a *waiting session*: a person whose sign-in ends pending now receives an ordinary session (amending
feature 002's "no session for pending people"), which a new per-request check accepts only for two endpoints (own
status, one-time access request with name and reason) plus sign-out, while every existing protected action keeps
refusing it because it still requires an active person; (2) an optional `expect_status` guard on admin actions (409 `already_handled` when someone else got there first) and three additive fields on existing admin responses
(request text, who added an allowlist entry, the list of audit actions) and a person filter on the audit
read; (3) HTML page routes with server-side gating (`/admin` is never delivered to a non-admin), strict
security headers on every response, and a friendly 404; (4) one small migration for the request text.
Caddy's catch-all now proxies to the API. Text from people is only ever written with `textContent`, never
as markup, and a test fails if the pages use any markup-injecting API, inline script or inline style.
See [research.md](research.md) for each decision and [contracts/](contracts/) for the interface.

## Technical Context

**Language/Version**: Python 3.12 (server); plain ES2020 JavaScript, HTML5 and CSS (no transpiling) for pages

**Primary Dependencies**: no new Python or JavaScript dependencies; FastAPI's `FileResponse` serves files, `<dialog>` provides confirmations

**Storage**: PostgreSQL `app.users` gets two nullable columns (`request_reason`, `request_submitted_at`) and a length check on the existing `name`; no new tables; nothing new in Redis

**Testing**: existing isolated `make test-api` (pytest, in-process ASGI client) for routes, gating, headers, the waiting-session and request flow, a deny-by-default walk of every route, and the additive admin fields; a source-lint test over the page files (no `innerHTML`/inline script/inline style/third-party URL); a manual browser walk-through in [quickstart.md](quickstart.md) for the visual and click-through checks (no headless browser dependency added)

**Target Platform**: modern evergreen browsers (desktop and phone) over HTTPS, served by the existing Compose stack behind Caddy

**Project Type**: Web service gaining a thin static front end (same container, same origin)

**Performance Goals**: each page under 2 s on a normal connection (spec SC-006); pages are small static files (target: under 60 KB total per page including script and style), no database call to deliver them except the admin gate lookup

**Constraints**: CSP with `script-src 'self'` (no inline), no third-party resources, session cookie stays `HttpOnly`, no admin function or data without an admin session, no allowlist information visible to signed-out visitors, English only

**Scale/Scope**: about 50 users, a few admins; 3 public views, 1 admin page with 4 tabs

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design (below).*

| Principle | Applies here? | Status |
| --- | --- | --- |
| I. Permission-First Security | Yes: screens in front of the allowlist gate | Pass, evaluated against v1.0.0 plus the v1.0.1 clarification made first by task T001 (waiting session; "every path MUST pass the allowlist" becomes "every path to an approved session"). Authorization stays in the API dependencies (status and role checked per request against the database); pages add no permission logic of their own, and `/admin` assets are additionally withheld server-side from non-admins. The sign-in form's responses are untouched, so the uniform-response rule holds. Deliberate change: pending people now get a session; it can only reach own status/request/sign-out, enforced deny-by-default and proven by a test that walks every route, and rejecting/suspending/removal still revokes it immediately |
| II. Data Isolation and Safe SQL | No | N/A (no operational data, no `serving` access) |
| III. Untrusted Content and Contained Execution | Yes: names, reasons and emails are untrusted text rendered into pages | Pass. Rendered only with `textContent`; length-limited and cleaned on input; strict CSP; a lint test prevents regressions |
| IV. Small Core, Pluggable Everything | Yes | Pass with one recorded deviation (below): static pages in the existing API container instead of the Next.js app; no new service |
| V. Verifiable Answers | No | N/A |
| VI. Observability and Auditability | Yes | Pass. The access request is audited (`access_requested`); every admin action keeps its existing audit record because the pages call the same actions; no new secret is logged or stored readable (waiting sessions store only token hashes, like all sessions) |
| Technology and Operational Constraints | Yes | Deviation recorded: the constitution names a Next.js chat UI. That remains the plan for chat (a later feature). This feature is a sign-in/admin shell for ~50 people, so plain static pages are used and can be absorbed or replaced then |

**Complexity Tracking**:

| Deviation | Why needed | Simpler/standard alternative rejected because |
|-----------|------------|-----------------------------------------------|
| Plain static pages instead of the Next.js UI named in the constitution | Operator wants an admin/sign-in UI now, before the chat UI exists; one developer, one VPS | Standing up Next.js, a Node container and a build pipeline now would add a second runtime and deployment surface for four small screens; the admin pages call the same API the future UI will, so nothing is lost |

**Post-design re-check**: Pass. [data-model.md](data-model.md) and [contracts/](contracts/) keep these
properties. Residual items are recorded in research: anyone with a Google account can now create a pending person plus a waiting session, bounded by the existing per-address verify limit and seven-day expiry (R-3); and the pages themselves are public files, so nothing in them may be secret (R-6).

## Project Structure

### Documentation (this feature)

```text
specs/003-web-sign-in-admin-ui/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md      # new and changed endpoints (additive)
│   └── pages.md         # routes, views, states, headers
└── tasks.md             # created by /speckit-tasks
```

### Source Code (repository root)

```text
api/
├── app/
│   ├── web/                         # NEW static pages (public files, no secrets)
│   │   ├── index.html               # sign-in, request, waiting, inside, messages (one page, views toggled by script)
│   │   ├── admin.html               # admin shell (served only to admins)
│   │   ├── 403.html, 404.html       # refusal and not-found pages
│   │   ├── static/                  # public: style.css, common.js, app.js, favicon.svg
│   │   └── admin-assets/            # admin-only: admin.js (served only to admins)
│   ├── pages.py                     # NEW: HTML routes, admin gating, 404 for non-API paths
│   ├── security_headers.py          # NEW: CSP and friends on every response
│   ├── auth/deps.py                 # CHANGED: current_person (active or pending) next to current_user (active only)
│   ├── auth/access.py               # NEW: GET /v1/access, POST /v1/access/request
│   ├── auth/service.py              # CHANGED: the gate gives a pending person a waiting session
│   ├── auth/routes.py               # CHANGED: Google callback sets the cookie for pending too
│   ├── admin/routes.py, service.py  # CHANGED: additive fields, audit person filter
│   ├── audit.py                     # CHANGED: new action access_requested
│   └── main.py                      # CHANGED: include pages router, headers middleware
├── tests/
│   ├── integration/test_access_request.py, test_access_status.py, test_pages.py   # NEW
│   ├── integration/test_admin_pages.py, test_admin_ui_api.py, test_stored_text_is_inert.py   # NEW
│   ├── integration/test_deny_by_default.py                                       # NEW: every route vs a waiting session
│   ├── integration/test_unapproved.py, test_google.py, test_audit.py             # CHANGED: pending now has a waiting session
│   └── unit/test_page_sources.py, test_security_headers.py, test_deps_status.py  # NEW
db/migrations/20261115020000_access_request.sql   # NEW
caddy/Caddyfile                                   # CHANGED: catch-all proxies to the API
docs/auth.md, README.md, specs/002-passwordless-auth/spec.md   # CHANGED: UI usage replaces curl; 002 FR-008 amendment noted
```

**Structure Decision**: the pages live inside the API package so the image, tests, headers and the admin
gate are one unit; Caddy keeps terminating HTTPS and proxies everything to the API. The two asset folders
are separate so only `static/` is public.
