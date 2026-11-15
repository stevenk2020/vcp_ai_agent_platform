# Contract: Operator commands and settings

## Create the first admin

Run on the server by someone with shell access to the Compose project:

```bash
docker compose exec api python -m app.cli create-first-admin --email you@example.com
```

| Situation | Behaviour |
| --- | --- |
| No admin exists | Creates the user as `admin`, status `active`; prints the email and a reminder to sign in with a code; audits `first_admin_created` (actor `cli`); exit code 0 |
| An admin already exists (status other than `rejected`) | Creates nothing, explains that an admin already exists and that further admins are added through the admin API; audits `first_admin_refused`; exit code 2 |
| The email is malformed | Exit code 1 with a message |
| Two runs at the same moment | One succeeds, the other behaves like "an admin already exists" (advisory lock) |

There is no setting, environment variable or file that grants admin rights; after the first admin
exists the command is inert. The admin signs in with an emailed code like anyone else, so the
`MAIL_BACKEND` must be able to deliver (in development read the code from the capture directory).

## Other commands

```bash
make secrets       # also creates api_rw_password, auth_pepper and the api_external template
make up            # builds and starts the api service, runs migrations
make test-api      # runs the integration tests in the api-test container (profile test)
```

## Settings

Non-secret settings come from the environment (see data-model.md "Configuration"). External
credentials come from `secrets/api_external` (dotenv, one `KEY=value` per line, empty value = not
configured):

| Key | Used for |
| --- | --- |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | sending codes when `MAIL_BACKEND=smtp` |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google sign-in; absent means the Google endpoints return `503 not_configured` |

Going live checklist: set `APP_ENV=production` and `MAIL_BACKEND=smtp`, fill `api_external`, create
the first admin, check `GET /v1/health` reports `mail_backend: smtp`.
