# =====================================================================
# DWTGBot Makefile — local dev convenience.
# Production deploy uses deploy/scripts/* and docker compose stacks.
# =====================================================================

PYTHON ?= python3.11
VENV   ?= .venv
PIP    := $(VENV)/bin/pip
PY     := $(VENV)/bin/python

COMPOSE_NL1 := docker compose -f deploy/nl1/docker-compose.yml --env-file deploy/nl1/.env
COMPOSE_NL2 := docker compose -f deploy/nl2/docker-compose.yml --env-file deploy/nl2/.env

.PHONY: help venv install dev-install lint fmt typecheck test \
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

install: venv ## Install prod dependencies
	$(PIP) install -r requirements/prod.txt

dev-install: venv ## Install dev dependencies
	$(PIP) install -r requirements/dev.txt

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
