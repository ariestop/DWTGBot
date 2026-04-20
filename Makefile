# =====================================================================
# DWTGBot Makefile — local dev convenience.
# Production deploy uses deploy/scripts/* and docker compose stacks.
# =====================================================================

PYTHON ?= python3.14
VENV   ?= .venv
PIP    := $(VENV)/bin/pip
PY     := $(VENV)/bin/python

COMPOSE_NL1 := docker compose -f deploy/nl1/docker-compose.yml --env-file deploy/nl1/.env
COMPOSE_NL2 := docker compose -f deploy/nl2/docker-compose.yml --env-file deploy/nl2/.env

.PHONY: help venv install dev-install lint fmt typecheck test \
        lock lock-check \
        up down logs ps restart \
        nl1-up nl1-down nl1-logs nl2-up nl2-down nl2-logs \
        migrate revision worker bot api \
        backup restore cleanup healthcheck \
        precommit-install

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ---------- Local Python env ----------
venv: ## Create local virtualenv
	$(PYTHON) -m venv $(VENV)
	$(PIP) install -U pip wheel

install: venv ## Install prod dependencies from lockfile
	$(PIP) install -r requirements/prod.lock

dev-install: venv ## Install dev dependencies from lockfile
	$(PIP) install -r requirements/dev.lock

# L8: transitive-pin lockfiles. `make lock` regenerates all three;
# `make lock-check` fails in CI if a human-edited requirements/*.txt
# drifts from requirements/*.lock. ``uv`` is invoked via
# ``python -m uv`` to avoid pinning the lock workflow to a particular
# PATH resolution (wheel installs differ between venv/global).
PY_LOCK ?= python3.14
LOCK_FLAGS := --python-version 3.14 --generate-hashes --quiet

lock: ## Regenerate requirements/*.lock from *.txt
	$(PY_LOCK) -m uv pip compile requirements/base.txt -o requirements/base.lock $(LOCK_FLAGS)
	$(PY_LOCK) -m uv pip compile requirements/dev.txt  -o requirements/dev.lock  $(LOCK_FLAGS)
	$(PY_LOCK) -m uv pip compile requirements/prod.txt -o requirements/prod.lock $(LOCK_FLAGS)

lock-check: ## Fail if *.lock drifts from *.txt (used in CI)
	@tmpdir=$$(mktemp -d); \
	for r in base dev prod; do \
	  $(PY_LOCK) -m uv pip compile requirements/$$r.txt -o $$tmpdir/$$r.lock $(LOCK_FLAGS); \
	  if ! diff -q requirements/$$r.lock $$tmpdir/$$r.lock >/dev/null; then \
	    echo "::error::requirements/$$r.lock is stale — run 'make lock'"; \
	    diff -u requirements/$$r.lock $$tmpdir/$$r.lock || true; \
	    rm -rf $$tmpdir; exit 1; \
	  fi; \
	done; \
	rm -rf $$tmpdir; \
	echo "lockfiles are up to date"

precommit-install: ## Install pre-commit git hooks
	$(VENV)/bin/pre-commit install

# ---------- Quality ----------
lint: ## Run ruff lint
	$(VENV)/bin/ruff check app

fmt: ## Format with ruff
	$(VENV)/bin/ruff format app
	$(VENV)/bin/ruff check --fix app

typecheck: ## Run mypy
	$(VENV)/bin/mypy app

test: ## Run pytest
	$(VENV)/bin/pytest

# ---------- Local processes ----------
bot: ## Run bot locally (requires .env)
	$(PY) -m app.main_bot

api: ## Run FastAPI locally
	$(PY) -m app.main_api

worker: ## Run arq worker locally
	$(PY) -m app.main_worker

migrate: ## Apply migrations
	$(VENV)/bin/alembic upgrade head

revision: ## Autogenerate revision: make revision m="msg"
	$(VENV)/bin/alembic revision --autogenerate -m "$(m)"

# ---------- Compose: NL-1 (control plane) ----------
nl1-up: ## Start NL-1 stack
	$(COMPOSE_NL1) up -d

nl1-down: ## Stop NL-1 stack
	$(COMPOSE_NL1) down

nl1-logs: ## Tail NL-1 logs
	$(COMPOSE_NL1) logs -f --tail=200

# ---------- Compose: NL-2 (media plane) ----------
nl2-up: ## Start NL-2 stack
	$(COMPOSE_NL2) up -d

nl2-down: ## Stop NL-2 stack
	$(COMPOSE_NL2) down

nl2-logs: ## Tail NL-2 logs
	$(COMPOSE_NL2) logs -f --tail=200

# ---------- Aggregate (used by install.sh menu) ----------
up: nl1-up nl2-up ## Start both stacks
down: nl1-down nl2-down ## Stop both stacks

# ---------- Ops scripts ----------
healthcheck: ## Run healthcheck script
	bash deploy/scripts/healthcheck.sh

backup: ## Run backup script
	bash deploy/scripts/backup.sh

restore: ## Run restore (interactive)
	bash deploy/scripts/restore.sh

cleanup: ## Run cleanup script
	bash deploy/scripts/cleanup.sh
