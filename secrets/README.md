# secrets/

One file per secret. **Nothing in this directory is tracked by git except this README**
(see `.gitignore`). Never paste secret values into issues, commits, chat, or logs.

`make secrets` (script: `scripts/gen-secrets.sh`) creates the five password files that do not exist
yet, with random values. It never overwrites an existing file.

| File | Created by | Consumers |
| --- | --- | --- |
| `postgres_superuser_password` | `make secrets` | `postgres`, `db-init` |
| `app_owner_password` | `make secrets` | `db-init`, `migrate` |
| `sync_writer_password` | `make secrets` | `db-init` (the future sync worker will also use it) |
| `agent_ro_password` | `make secrets` | `db-init` (the future Topic service will also use it) |
| `backup_ro_password` | `make secrets` | `db-init`, `backup` |
| `backup_age_public_key` | **operator** (`age-keygen`; public key only) | `backup` |
| `rclone_config` | **operator** (`rclone config` for the Google Drive remote `gdrive`) | `backup` |

The two operator-provided files are optional until you want backups: while either
`backup_age_public_key` or `rclone_config` is missing or empty, `make up` starts the stack without the
backup service.

Rotating a password: edit the file, then `make up` (the bootstrap step re-applies role passwords).

The source SQL Server credentials stay in `.env` (also ignored) and are not used by any service in
this feature.
