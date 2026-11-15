# HTTP contract additions (all additive; existing 002 behaviour unchanged)

Same conventions as feature 002: JSON errors `{"error","message"}`; unsafe methods require `Origin` = public origin.

## Changed: the sign-in gate (all sign-in paths)
When the allowlist gate ends `pending`, a waiting session is created and its cookie is set exactly like a normal one (`__Host-session`). Code verify returns `{"status":"pending"}` plus the cookie; Google callback redirects to `/?signin=pending` plus the cookie. Audit: `session_created` is recorded for it as for any session. Rejected/suspended/refused results still create nothing.

## Unchanged protection
`GET /v1/me`, `/v1/admin/*` and every other protected route keep requiring an **active** person; a waiting session gets the same `401` as no session. Only the two routes below accept it.

## New: `GET /v1/access`
Requires a session of an active or pending person.
- `200` `{"email", "status": "pending"|"active", "role": str|null, "submitted": bool, "name": str|null, "reason": str|null}` (`role` only when active).
- `401 {"error":"unauthenticated"}` for no, expired, revoked, rejected or suspended session.

## New: `POST /v1/access/request`
Requires a session of a **pending** person. Body `{"name": str|null (≤80), "reason": str|null (≤500)}`, extra fields refused.
- `201 {"submitted": true}` first time; audit `access_requested`.
- `200 {"submitted": false, "already_submitted": true}` if already submitted; stored text unchanged.
- `401` as above; `409 {"error":"not_pending"}` if the person is active (nothing to request); `422` for over-long text.

## Changed: admin actions (`approve`, `reject`, `suspend`, `role`)
New optional query parameter `expect_status` (`pending|active|suspended|rejected`). If given and the person's current status differs: `409 {"error":"already_handled","message":"Someone else already handled this person. Refresh the list."}`, nothing changed, nothing audited. Unknown value gives `422`. Omitted: behaviour exactly as in feature 002.

## Changed: `GET /v1/admin/users`
Each item additionally has `name`, `request_reason`, `request_submitted_at` (null when absent).

## Changed: `GET /v1/admin/allowlist`
Each item additionally has `added_by_email` (null when unknown or the adder was removed).

## Changed: `GET /v1/admin/audit`
New optional query `person` (case-insensitive prefix match on actor email or target, ≤254 chars). Response becomes `{"items":[...], "actions":[all action names]}`. Existing parameters (`limit`, `action`, `before`) and item fields are unchanged.

## Unchanged and reused by the pages
`GET /v1/health` (mail_backend), `GET /v1/me`, `POST /v1/auth/logout`, `POST /v1/auth/code/request|verify`, `GET /v1/auth/google/start`, admin `allowlist` add/remove, `users/{id}/approve|reject|suspend|role`.
