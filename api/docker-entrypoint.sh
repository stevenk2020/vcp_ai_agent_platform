#!/bin/sh
# Runs as root only long enough to copy the secrets this service needs into a memory-only directory
# owned by the unprivileged user (bind-mounted secret files keep the host owner and mode 0600, which
# the container user could not read), then drops privileges and starts the app.
set -eu

if [ "$(id -u)" = "0" ]; then
  dest=/run/api-secrets
  mkdir -p "$dest"
  for name in api_rw_password auth_pepper api_external; do
    cp "/run/secrets/$name" "$dest/$name"
    chmod 400 "$dest/$name"
    chown app:app "$dest/$name"
  done
  chown app:app "$dest"
  chmod 500 "$dest"
  SECRETS_DIR="$dest"
  export SECRETS_DIR
  exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
