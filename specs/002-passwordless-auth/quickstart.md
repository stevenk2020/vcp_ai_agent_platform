# Quickstart: Validate Passwordless Sign-in

End-to-end checks for each success criterion. Interfaces are in [contracts/](contracts/), tables in
[data-model.md](data-model.md).

## Prerequisites

- Feature 001 running: `make secrets && make up` (Postgres, Redis, migrations, Caddy with `make up-prod`)
- `make secrets` has created `api_rw_password`, `auth_pepper` and the `api_external` template
- Development settings: `APP_ENV=development`, `MAIL_BACKEND=capture` (default). Codes are written to
  the capture directory inside the `api` container: `docker compose exec api ls /var/mail-capture`
- No SMTP or Google credentials are needed for any scenario below; Google is exercised with a fake provider

## Scenario 1: First admin (SC-007)

```bash
docker compose exec api python -m app.cli create-first-admin --email admin@example.com
docker compose exec api python -m app.cli create-first-admin --email other@example.com
```

Expect: first command exit 0; second exit 2 and no new admin; `GET /v1/admin/audit` (after step 2
below) shows `first_admin_created` and `first_admin_refused`.

## Scenario 2: Sign in with a code and reach a protected endpoint (SC-001, SC-002)

1. `POST /v1/auth/code/request` with `{"email": "admin@example.com"}` returns `202`.
2. Read the code from the capture directory.
3. `POST /v1/auth/code/verify` with the code returns `200 signed_in`, role `admin`, and sets the cookie.
4. `GET /v1/me` with the cookie returns `200`.
5. Repeat the verify with the same code: `401 invalid_or_expired_code`. Request a new code and wait
   11 minutes (the tests advance a fake clock instead): `401`. Enter 5 wrong codes: the correct one
   then also returns `401`.

## Scenario 3: Allowlist and approval (SC-003, SC-005)

As the admin (cookie from scenario 2, `Origin` header set to the public origin):

1. `POST /v1/admin/allowlist` with `{"kind":"email","value":"alice@example.com","default_role":"user"}`.
2. Sign in as alice (request, verify): `signed_in`, role `user`.
3. As alice call `GET /v1/admin/allowlist`: `403 forbidden`.
4. As admin `DELETE /v1/admin/allowlist/{id}`; as alice `GET /v1/me`: `401` on the very next request.
5. Sign in as `bob@example.com` (not allowlisted): verify returns `200 pending`, no cookie; bob's
   `GET /v1/me` is `401`; `GET /v1/admin/users?status=pending` lists bob; approve him with a role and he
   can sign in; reject someone else and they cannot.

## Scenario 4: No leaking of who is allowed (SC-004)

Request codes for an allowlisted and a non-allowlisted address: both return the same `202` body.
`make test-api` also compares median response times over 200 trials (difference under 20%).

## Scenario 5: Abuse limits (SC-009)

Request codes for one address until `429 rate_limited` (default 5 per hour): no further mail appears
in the capture directory. Same for 21 requests from one IP. The message is identical for allowlisted
and non-allowlisted addresses.

## Scenario 6: Google with a stand-in provider

`make test-api` runs the Google flow with a fake identity provider: verified allowlisted email gets a
session, verified non-allowlisted email is pending, unverified email is refused, bad `state` fails.
Real Google needs the OAuth client from `secrets/api_external` (operator task).

## Scenario 7: Audit and secrets hygiene (SC-006, SC-008)

`make test-api` performs each audited action once and asserts exactly one record each, then searches
the database and the captured logs for any code, session token or Google token used during the run
and expects none. Inspect manually: `GET /v1/admin/audit` shows time, actor, action, outcome and IP.

## Scenario 8: Behind HTTPS (production only)

```bash
make up-prod
curl -fsS https://vcp.onepercentdata.site/v1/health
```

Expect `200` with `{"status":"ok","mail_backend":"capture"}` until SMTP is configured; the cookie
must be set with `Secure; HttpOnly; SameSite=Lax` (check with `curl -i` on a verify response).
