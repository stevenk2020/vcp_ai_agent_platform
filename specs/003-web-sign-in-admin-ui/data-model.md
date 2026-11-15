# Data Model: Web Pages for Sign-in, Access Request and Admin Management

Only `app.users` changes. All other entities are as in feature 002.

## `app.users` (changed)

| Column | Type | Rule |
| --- | --- | --- |
| `name` (existing) | text null | display name from the access request; `CHECK (name IS NULL OR char_length(name) BETWEEN 1 AND 80)` |
| `request_reason` (new) | text null | `CHECK (request_reason IS NULL OR char_length(request_reason) BETWEEN 1 AND 500)` |
| `request_submitted_at` (new) | timestamptz null | set together with `name`/`request_reason` on the one submission |

Rules:
- The request can be written once: only while `status = 'pending'` and `request_submitted_at IS NULL`. A person who submits an empty name and reason still counts as submitted (`request_submitted_at` set), so the form is not offered again.
- The columns are kept after approval, rejection or suspension (history for admins); nothing clears them.
- The existing `api_rw` table-level grants already cover the new columns; no new grants.

## Waiting session (no new storage)

A row in the existing `app.sessions` table whose user has `status = 'pending'`. Same token hash, expiry and revocation as any session. Whether it is accepted is decided per request by the route's dependency (`current_person` for the two access endpoints, `current_user` everywhere else), never by a flag on the row.

## State transitions touched

| From | Event | To |
| --- | --- | --- |
| pending (no request) | submit request | pending (request stored, `access_requested` audited) |
| pending (request stored) | submit again | unchanged, reply `already_submitted` |
| pending | sign-in (Google or code) | pending, now with a waiting session (new) |
| pending | admin approve / reject | active (the same session now works everywhere) / rejected (session revoked) (unchanged from 002) |
| suspended, rejected | admin approve with role ("reactivate") | active (unchanged from 002) |

## New audit action

`access_requested` (success): `actor_email` = the person, `target` = their email, `ip` = caller; detail holds only `{"has_name": bool, "has_reason": bool}`, never the text.
