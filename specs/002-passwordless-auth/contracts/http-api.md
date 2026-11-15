# Contract: HTTP API (auth and admin)

Base path `/v1`, JSON only, served by the `api` service behind Caddy at
`https://vcp.onepercentdata.site`. This is what later features and the future Chat/Admin UI rely on.

## Conventions

- Request and response bodies are `application/json`. Unknown fields are rejected (`422`).
- **Errors** share one shape: `{"error": "<code>", "message": "<human text>"}`. Codes used here:
  `invalid_request`, `invalid_or_expired_code`, `rate_limited`, `unauthenticated`, `forbidden`,
  `origin_not_allowed`, `not_found`, `conflict`, `last_admin`, `try_again_later`, `not_configured`.
- **Session cookie**: `__Host-session=<token>; Secure; HttpOnly; SameSite=Lax; Path=/; Max-Age=<SESSION_DAYS * 86400>` (604800 by default).
  The token is random and opaque; only its hash is stored.
- **CSRF**: every `POST`, `PUT`, `PATCH`, `DELETE` (including the sign-in endpoints) must carry an
  `Origin` header equal to `PUBLIC_ORIGIN`, otherwise `403 origin_not_allowed`. Browsers send it on
  their own; scripts and `curl` must add it.
- Email addresses are trimmed and compared case-insensitively; at most 254 characters.
- Messages never say whether an email is on the allowlist.

## Sign in

### `POST /v1/auth/code/request`

Body: `{"email": "person@example.com"}`

| Result | Meaning |
| --- | --- |
| `202` `{"message": "If this address can receive mail, a code has been sent."}` | Always for a syntactically valid address, allowlisted or not |
| `422 invalid_request` | Not a valid email address |
| `429 rate_limited` + `Retry-After` | Per-email or per-IP cap reached; no mail sent; same message for every address |
| `503 try_again_later` | Mail could not be sent; the code is cancelled |
| `503 not_configured` | Email codes are switched off (`MAIL_BACKEND=disabled`); nothing is sent or stored, use Google |

Side effects: cancels earlier unused codes for the email, stores the HMAC of a new 6-digit code
(valid 10 minutes), sends the mail, audits `code_requested` (or `code_send_failed`).

### `POST /v1/auth/code/verify`

Body: `{"email": "...", "code": "123456"}` (`code` is exactly 6 digits).

| Result | Meaning |
| --- | --- |
| `200` `{"status": "signed_in", "role": "user"}` + `Set-Cookie` | Code correct; email is allowlisted, already active, or was suspended only by allowlist removal and an entry matches again |
| `200` `{"status": "pending"}` (no cookie) | Code correct; not allowlisted; now in the approval queue |
| `200` `{"status": "refused"}` (no cookie) | Code correct, but an admin suspended or rejected the account |
| `401 invalid_or_expired_code` | Wrong, expired, already used, cancelled or locked code (all identical) |
| `429 rate_limited` | Too many verify attempts from this IP |

A correct code is consumed. The fifth wrong attempt on a code cancels it. Audits `code_verified`,
`code_failed`, `code_locked`, `signin_pending`, `signin_refused`, `session_created`.

### `GET /v1/auth/google/start`

Redirects (`302`) to Google's consent page with PKCE and `state`; sets a short-lived HttpOnly cookie
holding the state and verifier. `503 not_configured` when no Google client is configured.

### `GET /v1/auth/google/callback?code=...&state=...`

Verifies `state`, exchanges the code, verifies the ID token (signature, issuer, audience, expiry) and
requires `email_verified = true`. Outcomes mirror `verify`: a redirect to `/?signin=ok` with the
session cookie, `/?signin=pending`, or `/?signin=refused`; any failure redirects to `/?signin=error`
(unverified email, bad state, provider error). Google tokens are discarded and never stored or logged.

### `POST /v1/auth/logout`

`204`. Revokes the current session and clears the cookie. Idempotent for already-ended sessions.

### `GET /v1/me`

The protected check every later feature can call.

| Result | Meaning |
| --- | --- |
| `200` `{"id": "...", "email": "...", "role": "user", "status": "active"}` | Valid session |
| `401 unauthenticated` | No cookie, unknown, expired or revoked session, or user not active |

## Admin (role `admin` only; others get `403 forbidden`, no session gets `401`)

### Allowlist

| Method and path | Body | Result |
| --- | --- | --- |
| `GET /v1/admin/allowlist` | none | `200` `{"items": [{"id", "kind", "value", "default_role", "added_by", "created_at"}]}` |
| `POST /v1/admin/allowlist` | `{"kind": "email" or "domain", "value": "...", "default_role": "admin" or "builder" or "user"}` | `201` the entry; `409 conflict` if it already exists; `422` for a malformed value |
| `DELETE /v1/admin/allowlist/{id}` | none | `204`; sessions of active users who matched only this entry are revoked and those users suspended (reason `allowlist_removed`) in the same transaction; `404`; `409 last_admin` if it would leave no admin |

### People and the approval queue

| Method and path | Body | Result |
| --- | --- | --- |
| `GET /v1/admin/users?status=pending` | none | `200` `{"items": [{"id", "email", "role", "status", "created_at", "last_login_at"}]}`; `status` is optional and one of `pending`, `active`, `suspended`, `rejected` |
| `POST /v1/admin/users/{id}/approve` | `{"role": "user"}` | `200` the user, now `active` with that role |
| `POST /v1/admin/users/{id}/reject` | none | `200` the user, now `rejected`; sessions revoked |
| `POST /v1/admin/users/{id}/suspend` | none | `200`; suspended with reason `admin`; sessions revoked; `409 last_admin` if it would leave no admin |
| `POST /v1/admin/users/{id}/role` | `{"role": "builder"}` | `200`; applies on the user's next request; `409 last_admin` when demoting the last admin |

### Audit

`GET /v1/admin/audit?limit=100&action=...&before=<id>` returns `{"items": [...]}` newest first
(`limit` 1 to 500). Items never contain codes or tokens.

## Health

`GET /v1/health` (no session): `200` `{"status": "ok", "mail_backend": "capture", "smtp" or "disabled"}` when
Postgres and Redis answer, `503` otherwise. Used by the container healthcheck.
