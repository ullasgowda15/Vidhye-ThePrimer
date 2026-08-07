# The Primer — developer entrypoints.
#
# Every target assumes you are in the repo root. `PY` points at the interpreter
# that has the backend requirements installed; override it if you manage your
# venv differently:
#
#     make test PY=python

SHELL := /bin/sh

# Prefer the repo-local venv when it exists (Windows uses Scripts/, POSIX bin/).
ifeq ($(OS),Windows_NT)
  VENV_PY := .venv/Scripts/python.exe
else
  VENV_PY := .venv/bin/python
endif
PY ?= $(if $(wildcard $(VENV_PY)),$(VENV_PY),python)
ABS_PY := $(abspath $(PY))

COMPOSE := docker compose -f infra/docker-compose.yml

.DEFAULT_GOAL := help
.PHONY: help venv install up down logs migrate migrations run worker front build \
        test test-backend test-frontend lint lint-backend lint-frontend format \
        typecheck schema client superuser hello-beat ping clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- setup -----------------------------------------------------------------

venv: ## Create the local virtualenv
	python -m venv .venv

install: ## Install backend + frontend dependencies
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r backend/requirements-dev.txt
	cd frontend && npm install

# --- infrastructure --------------------------------------------------------

up: ## Start Postgres (pgvector) + Redis
	$(COMPOSE) up -d

down: ## Stop local infrastructure
	$(COMPOSE) down

logs: ## Tail infrastructure logs
	$(COMPOSE) logs -f

# --- backend ---------------------------------------------------------------

migrations: ## Generate migrations for model changes
	cd backend && $(ABS_PY) manage.py makemigrations

migrate: ## Apply migrations
	cd backend && $(ABS_PY) manage.py migrate

run: ## Serve the API under ASGI (uvicorn) — required for SSE
	cd backend && $(ABS_PY) -m uvicorn config.asgi:application --reload --port 8000

worker: ## Start the Celery worker
	cd backend && $(ABS_PY) -m celery -A config worker -l info --pool=solo

superuser: ## Create an admin login
	cd backend && $(ABS_PY) manage.py createsuperuser

hello-beat: ## Generate one validated story Beat via Claude (needs ANTHROPIC_API_KEY)
	cd backend && $(ABS_PY) manage.py hello_beat

ping: ## Round-trip a task through Celery to prove the worker is live
	cd backend && $(ABS_PY) manage.py shell -c \
		"from apps.common.tasks import ping; print(ping.delay().get(timeout=20))"

# --- frontend --------------------------------------------------------------

front: ## Start the Vite dev server
	cd frontend && npm run dev

build: ## Production build of the PWA
	cd frontend && npm run build

# --- API contract ----------------------------------------------------------

schema: ## Export the OpenAPI schema from Django
	cd backend && $(ABS_PY) manage.py spectacular --format openapi-json --file ../frontend/openapi.json

client: schema ## Regenerate the typed TS client from the schema
	cd frontend && npm run client

# --- quality ---------------------------------------------------------------

test: test-backend test-frontend ## Run all tests

test-backend:
	cd backend && $(ABS_PY) -m pytest

test-frontend:
	cd frontend && npm run test

lint: lint-backend lint-frontend ## Lint + format-check everything

lint-backend:
	cd backend && $(ABS_PY) -m ruff check .
	cd backend && $(ABS_PY) -m ruff format --check .

lint-frontend:
	cd frontend && npm run lint

typecheck: ## Type-check both sides
	cd backend && $(ABS_PY) -m mypy .
	cd frontend && npm run typecheck

format: ## Autoformat everything
	cd backend && $(ABS_PY) -m ruff format .
	cd backend && $(ABS_PY) -m ruff check --fix .
	cd frontend && npm run format

clean: ## Remove build artifacts and caches
	rm -rf frontend/dist backend/staticfiles
	rm -rf backend/.pytest_cache backend/.ruff_cache backend/.mypy_cache
