# Sign-in, allowlist and admin (features 002 and 003)

Only people an admin has approved can sign in, without any password. Two ways in, one gate:

- **Google** (`GET /v1/auth/google/start`): the person signs in with a Google account; only an email
  Google reports as verified counts.
- **Emailed code** (`POST /v1/auth/code/request` then `/verify`): a 6-digit code valid 10 minutes, 5 wrong
  attempts kill it, one use. Can be switched off (`MAIL_BACKEND=disabled`, the production start).

After the email is proven the allowlist is checked: allowed people get a session (a 7-day cookie).
Everyone else is created as `pending`, waits in the approval queue, and holds a **waiting session**: it
proves who they are and lets them see their status and submit an access request (name and reason, once),
and it opens nothing else. After an admin approves them, a reload takes them inside with the chosen role.
Removing someone from the allowlist, or rejecting or suspending them, ends their sessions (waiting ones
included) on their very next request. Rule of the constitution (v1.0.1): every protected action requires an
approved person; only `GET /v1/access`, `POST /v1/access/request` and sign-out accept a waiting session, and
`api/tests/integration/test_deny_by_default.py` walks every route to prove it.

## The web pages (feature 003)

Everything is at `https://vcp.onepercentdata.site/`:

| Page | Who | What |
| --- | --- | --- |
| `/` | everyone | "Sign in with Google" (the email-code form appears only when mail delivery is on); approved people see the placeholder inside area with their role and a sign-out button; waiting people see the access request page |
| `/admin` | admins only | **Waiting** (approve with a role or reject, each with a confirmation), **Allowlist** (add or remove an email or domain; removal ends that person's sessions at once), **People** (change role, suspend, reactivate with a chosen role), **Audit log** (newest first, filter by action and by email, "Older events") |

Signed-out visitors opening `/admin` are sent to `/`; anyone who is not an admin sees a refusal page and never
receives the admin script. If two admins act on the same person, the second sees "Someone else already handled
this person" and nothing changes twice. Pages are plain files in `api/app/web/` (no framework, no build step);
all responses carry a strict content security policy (no inline script, nothing from other sites).

The interface is in `specs/002-passwordless-auth/contracts/http-api.md`; the design in
`specs/002-passwordless-auth/`.

## Roles and states

Roles: `admin`, `builder`, `user`. People are `pending`, `active`, `suspended` or `rejected`.

- A person suspended only because their allowlist entry was removed is activated again when an entry
  matches (their next sign-in). Someone suspended or rejected by an admin stays blocked until an admin
  approves them, even if they are on the allowlist.
- The last active admin cannot be removed, suspended, rejected or demoted (`409 last_admin`).

## Create the first admin (once)

```bash
docker compose exec api python -m app.cli create-first-admin --email you@example.com
```

Exit 0 created; 2 an admin already exists (nothing created, audited as `first_admin_refused`); 1 malformed
email. There is no setting or file that grants admin rights. The admin then signs in like anyone else:
with Google, or an emailed code if enabled, using that same address.

## Production, Google only (the starting setup)

In `.env`: `APP_ENV=production` and `MAIL_BACKEND=disabled`. In `secrets/api_external`:
`GOOGLE_CLIENT_ID=...` and `GOOGLE_CLIENT_SECRET=...` (no quotes, one per line). The Google client's
redirect URI must be exactly `https://vcp.onepercentdata.site/v1/auth/google/callback`. Then `make up-prod`.
The service refuses to start in production if no sign-in path is configured, or if the capture mail
sink is selected. `GET https://vcp.onepercentdata.site/v1/health` shows the mode (`mail_backend`).

To add emailed codes later, fill `SMTP_HOST`, `SMTP_PORT` (587, STARTTLS), `SMTP_USER`, `SMTP_PASSWORD`,
`SMTP_FROM` in `secrets/api_external`, set `MAIL_BACKEND=smtp` and `make up-prod`.

## Break-glass: managing people with curl

Use the admin area first. If the pages are down, admin actions still work as `POST`/`PUT`/`PATCH`/`DELETE`
requests with the session cookie **and** an `Origin` header. After signing in with Google, copy the cookie
`__Host-session` from the browser's developer tools (Application → Cookies):

```bash
O=https://vcp.onepercentdata.site
S='<value of __Host-session>'
H=(-H "Origin: $O" -H 'Content-Type: application/json' --cookie "__Host-session=$S")

curl -sS "${H[@]}" $O/v1/me
curl -sS "${H[@]}" -d '{"kind":"email","value":"colleague@example.com","default_role":"user"}' $O/v1/admin/allowlist
curl -sS "${H[@]}" "$O/v1/admin/users?status=pending"                       # the approval queue
curl -sS "${H[@]}" -X POST -d '{"role":"user"}' "$O/v1/admin/users/<id>/approve?expect_status=pending"
curl -sS "${H[@]}" -X POST "$O/v1/admin/users/<id>/reject?expect_status=pending"
curl -sS "${H[@]}" -X POST "$O/v1/admin/users/<id>/suspend"
curl -sS "${H[@]}" -X POST -d '{"role":"builder"}' $O/v1/admin/users/<id>/role
curl -sS "${H[@]}" -X DELETE $O/v1/admin/allowlist/<entry-id>
curl -sS "${H[@]}" "$O/v1/admin/audit?limit=50&action=signin_pending&person=alice"
```

`expect_status` is optional: when given, the call fails with `409 already_handled` if the person's status
is no longer what you saw. Treat the cookie value like a password.

## Development (emailed codes, no real email)

With `MAIL_BACKEND=capture` (the default outside production) the code is written to a file instead of
being mailed: `docker compose exec api sh -c 'cat /var/mail-capture/*.json'`. Files older than an
hour are deleted. The capture sink is refused in production.

## Tests

`make test-api` (optionally `ARGS="-k signin"`) runs the API tests in an isolated Compose project
(`platform-apitest`, own Postgres and Redis, removed afterwards); it never touches the live database.
`make test` still runs the data-layer suite.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Blank page or old page after a deploy | the `api` image was not rebuilt: `make up-prod`; then hard-reload the browser |
| Console shows a CSP violation | a page loads something from outside the site or uses inline script/style; `make test-api ARGS="-k page_sources"` names the file and line |
| `/admin` shows "You do not have access" | the signed-in person is not an active admin; ask an admin to change the role in People |
| Waiting person still sees the request page after approval | reload; if it persists, check `GET /v1/access` for their status |
| `403 origin_not_allowed` | add `-H "Origin: https://vcp.onepercentdata.site"` to every non-GET request |
| `401 unauthenticated` right after signing in | the cookie is `Secure`: use HTTPS; expired after 7 days; or the person was removed or suspended |
| Sign-in ends at `/?signin=pending` | the person is not on the allowlist: approve them in the queue, or add their email or domain |
| `/?signin=refused` | an admin suspended or rejected them; approve them again |
| `/?signin=error` | Google did not verify the email, the redirect expired (10 minutes), or the client settings are wrong |
| `503 not_configured` on code request | email codes are switched off (`MAIL_BACKEND=disabled`): use Google |
| `429 rate_limited` | too many codes or attempts; wait for `Retry-After` seconds |
| Service will not start | read `docker compose logs api`: it names the missing setting |
