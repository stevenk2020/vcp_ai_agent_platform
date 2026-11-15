# Operator interface for the foundation layer.
# Contract: specs/001-foundation-data-layer/contracts/operator-commands.md
#
# Usage notes:
#   make test ARGS="-k permissions"   (never `make test -- -k ...`; make would treat -k as its own option)

SHELL := /bin/bash
.DEFAULT_GOAL := help

ARGS ?=

# Compose files: the backup service is only loaded once the operator has provided an age public key
# (Compose cannot declare optional secret files; see research R-6).
COMPOSE_FILES := -f compose.yaml
BACKUP_ENABLED := $(shell test -s secrets/backup_age_public_key && test -f compose.backup.yaml && echo yes)
ifeq ($(BACKUP_ENABLED),yes)
COMPOSE_FILES += -f compose.backup.yaml
endif
DC := docker compose $(COMPOSE_FILES)
MIN_COMPOSE := 2.29.0
# Note: `up --wait` fails when a one-shot service (db-init, migrate) exits 0, so `up` starts with
# `up -d` and then runs scripts/wait-healthy.sh (healthy for long-running, exit 0 for one-shot).

.PHONY: help secrets check-compose up up-prod down migrate backup restore-test test scan-secrets

help:
	@echo "targets: secrets up up-prod down migrate backup restore-test test scan-secrets"

secrets:
	@bash scripts/gen-secrets.sh

check-compose:
	@v="$$(docker compose version --short 2>/dev/null | sed 's/^v//')"; \
	if [ -z "$$v" ]; then echo "docker compose not available (is your user in the docker group?)"; exit 1; fi; \
	if [ "$$(printf '%s\n%s\n' "$(MIN_COMPOSE)" "$$v" | sort -V | head -n1)" != "$(MIN_COMPOSE)" ]; then \
	  echo "docker compose $$v is older than $(MIN_COMPOSE)"; exit 1; fi

up: secrets check-compose
	@if [ "$(BACKUP_ENABLED)" != "yes" ]; then echo "backup disabled: no age key yet"; fi
	$(DC) up -d
	@bash scripts/wait-healthy.sh $(COMPOSE_FILES)

up-prod: secrets check-compose
	@if [ "$(BACKUP_ENABLED)" != "yes" ]; then echo "backup disabled: no age key yet"; fi
	$(DC) --profile prod up -d
	@bash scripts/wait-healthy.sh $(COMPOSE_FILES) --profile prod

down: check-compose
	$(DC) --profile prod down

migrate:
	@echo "not implemented yet (T043)"; exit 1

backup:
	@echo "not implemented yet (T037)"; exit 1

restore-test:
	@echo "not implemented yet (T037)"; exit 1

test:
	@cd tests && uv run pytest $(ARGS)

scan-secrets:
	@echo "not implemented yet (T027)"; exit 1
