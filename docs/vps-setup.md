# VPS setup (this machine is the production VPS)

Development and production share one host and one Docker daemon: `make up` for the base stack,
`make up-prod` additionally starts Caddy with HTTPS on `vcp.onepercentdata.site`.

## 1. Prerequisites (done once)

- Docker Engine and the Compose plugin, `make`, `uv`, `ufw`.
- Your user is in the `docker` group **and the shell that runs `make` started after you were added**
  (VS Code Remote keeps old group lists until `vscode-server` is restarted: `pkill -f vscode-server`
  and reconnect, or `newgrp docker`). Check with `id` and `docker ps`.
- DNS: an `A` record for `vcp.onepercentdata.site` points at this host's public IP
  (`dig +short vcp.onepercentdata.site` must print that IP).

## 2. Firewall

This machine is reached over SSH / VS Code Remote: **allow SSH before enabling the firewall**, or you
will lock yourself out.

```bash
sudo ufw allow OpenSSH        # or: sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw status               # check the three rules are listed BEFORE enabling
sudo ufw enable
```

Docker publishes ports by editing `iptables` directly and **bypasses `ufw`**. That is why only Caddy
publishes public ports (80 and 443), Postgres is bound to `127.0.0.1` only, and Redis publishes
nothing. Do not add `ports:` to other services without checking them against this rule
(`tests/foundation/test_secrets_scope.py` fails if you do).

## 3. Start in production mode

```bash
ss -ltnp | grep -E ':(80|443) ' || echo "ports 80/443 are free"   # nothing else may listen here
make secrets
make up-prod
docker compose ps
curl -fsS https://vcp.onepercentdata.site/healthz
```

Expected: every service healthy and the `curl` returns HTTP 200 over a valid certificate.

## 4. Troubleshooting HTTPS

| Symptom | Check |
| --- | --- |
| Certificate not issued | `docker compose logs caddy` (look for ACME errors) |
| Challenge fails | `dig +short vcp.onepercentdata.site` must be this host's public IP; ports 80 and 443 reachable from the internet (`sudo ufw status`, provider firewall) |
| Port already in use | `ss -ltnp \| grep -E ':(80\|443) '` and stop the other service |
| Browser works but `/healthz` 404 | the Caddyfile did not load; `docker compose logs caddy` |
| Rate limited by the CA | wait; Caddy retries and keeps the staging/production state in the `caddy_data` volume (do not delete it) |

Caddy sends `/v1/*` **and every other path** (the web pages, `/admin`, the 404 page) to the `api` container; only `/healthz` is answered by Caddy itself. After changing `caddy/Caddyfile`, recreate Caddy so the bind-mounted file is re-read: `docker compose -f compose.yaml -f compose.backup.yaml --profile prod up -d --force-recreate --no-deps caddy`.

Caddy keeps its certificates in the `caddy_data` volume; losing it only means re-issuing them.

## 5. After a reboot

All services use `restart: unless-stopped`, so the stack comes back with Docker. Run
`docker compose ps` to confirm. If `backup` is `unhealthy`, see `docs/restore.md`.
