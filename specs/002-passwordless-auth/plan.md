# Implementation Plan: Passwordless Sign-in with Admin Allowlist

**Branch**: `002-passwordless-auth` (working on `main`; no feature branch created) | **Date**: 2026-11-15 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-passwordless-auth/spec.md`

## Summary

Add the platform's first application service, `api` (the future Agent Platform backend), with the
sign-in, session, allowlist, approval and audit behaviour from the spec. The service is a Python 3.12
FastAPI app that talks to Postgres through its own least-privilege role (`api_rw`) and uses Redis for
rate-limit counters. Emailed 6-digit codes and Google sign-in both end in the same allowlist gate;
sessions are random tokens kept only as hashes, carried in an HttpOnly Secure cookie and re-checked
against the database on every request, so removing someone revokes them on their next request. Codes
are stored as HMAC values keyed by a server secret (a plain hash of 6 digits would be trivially
reversible). Time and mail are injected, so expiry and delivery are testable without waiting or
sending real email. The first admin is created by a one-time command on the server. See
[research.md](research.md) for each decision and [contracts/](contracts/) for the interface.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI + uvicorn, Pydantic v2, psycopg 3 with `psycopg_pool` (async), redis-py (asyncio), `email-validator`, `google-auth` (ID token verification), `httpx` (Google token exchange and tests); standard library `smtplib` for SMTP delivery

**Storage**: PostgreSQL 16 (schema `app`: `users`, `allowlist`, `email_codes`, `sessions`, `audit_events`), Redis 7 (rate-limit counters only, no durable data)

**Testing**: pytest + pytest-asyncio; integration tests run **inside the compose network** in a one-off `api-test` container (real Postgres and Redis, in-process ASGI client, fake clock, capturing mailer, fake Google provider)

**Target Platform**: Linux containers on the existing single-host Docker Compose stack, behind Caddy

**Project Type**: Web service (backend API only; no UI in this feature)

**Performance Goals**: sign-in code verification and session check each under 100 ms at 50 concurrent users (about 50 users total in production); session check adds one indexed database lookup per request

**Constraints**: no readable code, session token or Google token in storage or logs; same visible response and no consistent timing difference for allowlisted vs non-allowlisted emails; revocation takes effect on the very next request; `api_rw` has no access outside the tables it needs; the capture mailer must refuse to run in production mode

**Scale/Scope**: about 50 users, a few admins, tens of sign-ins per day

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design (below).*

| Principle | Applies here? | Status |
| --- | --- | --- |
| I. Permission-First Security | Yes: this feature is the identity layer; passwordless, allowlist, revocation, uniform login response | Pass. Role checks live in the API dependency that guards each route (service-side, not in a UI or prompt); sessions are re-validated against the database on every request; response and timing equalised between allowed and unknown emails |
| II. Data Isolation and Safe SQL | Partly: a new database role must not reach `serving` or `kb` | Pass. `api_rw` gets rights on named `app` tables only (migration grants), no default privileges, nothing on `serving`/`kb`; the permission matrix test is extended |
| III. Untrusted Content and Contained Execution | Partly: external input (email addresses, Google ID tokens) | Pass. Email is validated and normalised; Google tokens are verified (signature, audience, issuer, expiry, `email_verified`) and never stored; no code execution, no Docker socket |
| IV. Small Core, Pluggable Everything | Yes | Pass. One new service; mailer and Google are small interfaces (`Mailer`, `IdentityProvider`) with fakes for tests; no new infrastructure beyond what exists |
| V. Verifiable Answers | No | N/A |
| VI. Observability and Auditability | Yes: audit trail, no secrets in logs, cap on abuse | Pass. Append-only `audit_events` (api role cannot update or delete), hashes only, structured logs scrubbed of codes and tokens, per-request actor and IP recorded |
| Technology and Operational Constraints | Yes | Pass. Python/FastAPI per the constitution; secrets via files under `/run/secrets` (new: `api_rw_password`, `auth_pepper`, `api_external`); a new role is added through the bootstrap as contract rule 3 of feature 001 requires; migrations through dbmate |

No violations; Complexity Tracking is empty.

**Post-design re-check**: Pass. The design in [data-model.md](data-model.md) and
[contracts/](contracts/) keeps these properties. Two residual items are recorded in research:
the cookie cannot be hardened beyond the browser's rules if the operator ever serves it over plain
HTTP (R-9, HTTPS is mandatory in production), and sending codes to arbitrary addresses is bounded
only by the rate caps (R-6).

## Project Structure

### Documentation (this feature)

```text
specs/002-passwordless-auth/
├── plan.md              # This file
├── research.md          # Phase 0: decisions and alternatives
├── data-model.md        # Phase 1: tables, states, rules
├── quickstart.md        # Phase 1: validation scenarios
├── contracts/
│   ├── http-api.md      # endpoints, request/response, errors, cookie
│   └── operator-cli.md  # first-admin command and settings
└── tasks.md             # Phase 2 (/speckit-tasks, not created here)
```

### Source Code (repository root)

```text
api/
├── Dockerfile                  # multi-stage: runtime image and a `test` target with dev deps
├── pyproject.toml
├── app/
│   ├── main.py                 # app factory, routers, health
│   ├── config.py               # settings from env and /run/secrets
│   ├── db.py                   # async pool, small query helpers
│   ├── clock.py                # Clock protocol (system clock; fake in tests)
│   ├── security.py             # token generation, hashing, HMAC of codes, constant-time compare
│   ├── rate_limit.py           # Redis fixed-window counters
│   ├── mailer.py               # Mailer protocol, SMTP backend, capture backend
│   ├── google.py               # IdentityProvider protocol, Google OIDC implementation
│   ├── audit.py                # audit writer and logging scrubber
│   ├── auth/                   # code request/verify, Google flow, sessions, dependencies
│   ├── admin/                  # allowlist, approval queue, role and status changes, audit read
│   └── cli.py                  # create-first-admin
└── tests/                      # unit and integration tests (run in the api-test container)
db/
├── bootstrap/                  # add role api_rw and its grants (extends feature 001)
└── migrations/                 # users, allowlist, email_codes, sessions, audit_events
caddy/Caddyfile                 # proxy /v1/* to the api service
compose.yaml                    # add api and api-test (profile `test`)
scripts/gen-secrets.sh          # also create api_rw_password, auth_pepper, api_external template
```

**Structure Decision**: One Python package `api/app` that the later features (conversations, topic
tools, feedback) extend with more routers; auth and admin are separate sub-packages so each can be
tested alone. Migrations and role bootstrap stay under `db/` where feature 001 put them, so the data
layer keeps a single owner. The service runs as one container behind Caddy; no worker process is
needed because every operation is request-driven.

## Complexity Tracking

No constitution violations to justify.
