# Operator interface for the foundation layer.
# Contract: specs/001-foundation-data-layer/contracts/operator-commands.md
#
# Usage notes:
#   make test ARGS="-k permissions"   (never `make test -- -k ...`; make would treat -k as its own option)

SHELL := /bin/bash
.DEFAULT_GOAL := help

ARGS ?=

# Compose files: the backup service is only loaded once the operator has provided BOTH the age public key
# and the rclone config
# (Compose cannot declare optional secret files; see research R-6).
COMPOSE_FILES := -f compose.yaml
BACKUP_ENABLED := $(shell test -s secrets/backup_age_public_key && test -s secrets/rclone_config && test -f compose.backup.yaml && echo yes)
ifeq ($(BACKUP_ENABLED),yes)
COMPOSE_FILES += -f compose.backup.yaml
endif
DC := docker compose $(COMPOSE_FILES)
MIN_COMPOSE := 2.29.0
# Note: `up --wait` fails when a one-shot service (db-init, migrate) exits 0, so `up` starts with
# `up -d --build` (rebuilds changed images, so a code change is never served by a stale image) and then
# runs scripts/wait-healthy.sh (healthy for long-running, exit 0 for one-shot).

.PHONY: help secrets check-compose up up-prod down migrate backup restore-test test test-api test-sync scan-secrets source-secret sync-status sync-now

help:
	@echo "targets: secrets up up-prod down migrate backup restore-test test test-api test-sync scan-secrets source-secret sync-status sync-now"

secrets:
	@bash scripts/gen-secrets.sh

check-compose:
	@v="$$(docker compose version --short 2>/dev/null | sed 's/^v//')"; \
	if [ -z "$$v" ]; then echo "docker compose not available (is your user in the docker group?)"; exit 1; fi; \
	if [ "$$(printf '%s\n%s\n' "$(MIN_COMPOSE)" "$$v" | sort -V | head -n1)" != "$(MIN_COMPOSE)" ]; then \
	  echo "docker compose $$v is older than $(MIN_COMPOSE)"; exit 1; fi

up: secrets check-compose
	@if [ "$(BACKUP_ENABLED)" != "yes" ]; then echo "backup disabled: needs both secrets/backup_age_public_key and secrets/rclone_config"; fi
	$(DC) up -d --build
	@bash scripts/wait-healthy.sh $(COMPOSE_FILES)

up-prod: secrets check-compose
	@if [ "$(BACKUP_ENABLED)" != "yes" ]; then echo "backup disabled: needs both secrets/backup_age_public_key and secrets/rclone_config"; fi
	$(DC) --profile prod up -d --build
	@bash scripts/wait-healthy.sh $(COMPOSE_FILES) --profile prod

down: check-compose
	$(DC) --profile prod down

migrate:
	$(DC) run --rm migrate

backup:
	@if [ "$(BACKUP_ENABLED)" != "yes" ]; then echo "backup disabled: provide secrets/backup_age_public_key and secrets/rclone_config first (docs/restore.md)"; exit 1; fi
	$(DC) exec -T backup /usr/local/bin/backup.sh

restore-test:
	@bash scripts/restore-test.sh

test:
	@cd tests && uv run pytest $(ARGS)

test-api:
	@ARGS="$(ARGS)" bash scripts/test-api.sh

scan-secrets:
	@bash scripts/scan-secrets.sh

test-sync:
	@ARGS="$(ARGS)" bash scripts/test-sync.sh

source-secret:
	@bash scripts/source-secret-from-env.sh $(ARGS)

sync-status:
	@$(DC) exec -T sync python -m sync status

sync-now:
	@$(DC) exec -T sync python -m sync run sales_contract $(ARGS)
