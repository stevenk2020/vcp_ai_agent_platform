#!/bin/sh
# Runs as root only long enough to copy the secrets this service needs into a memory-only directory
# owned by the unprivileged user (bind-mounted secret files keep the host owner and mode 0600),
# then drops privileges and starts the worker.
set -eu

if [ "$(id -u)" = "0" ]; then
  dest=/run/sync-secrets
  mkdir -p "$dest"
  for name in sync_writer_password source_sqlserver; do
    cp "/run/secrets/$name" "$dest/$name"
    chmod 400 "$dest/$name"
    chown syncer:syncer "$dest/$name"
  done
  chown syncer:syncer "$dest"
  chmod 500 "$dest"
  SECRETS_DIR="$dest"
  export SECRETS_DIR
  exec setpriv --reuid=syncer --regid=syncer --init-groups "$@"
fi
exec "$@"
