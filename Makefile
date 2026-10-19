.DEFAULT_GOAL := run
PYTHON := .venv/bin/python3
UV := uv
export GIT_COMMIT := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)

# ── Help ──────────────────────────────────────────────────────────────────────

.PHONY: help
help:
	@echo ""
	@echo "  Synatyx Context Engine"
	@echo ""
	@echo "  Default"
	@echo "    make             Start everything and tail logs (same as make run)"
	@echo "    make run         Start all services and tail mcp + gc logs"
	@echo ""
	@echo "  Docker"
	@echo "    make up          Start all services (detached)"
	@echo "    make down        Stop and remove containers"
	@echo "    make restart     Down then up"
	@echo "    make logs        Tail mcp + gc container logs"
	@echo "    make migrate     Run Alembic migrations inside Docker"
	@echo "    make build       Rebuild Docker images"
	@echo ""
	@echo "  Local dev"
	@echo "    make install     Create venv and install all dependencies"
	@echo "    make mcp         Run MCP stdio server locally"
	@echo "    make dev         Run GraphQL + HTTP server locally (port 8000)"
	@echo ""
	@echo "  Quality"
	@echo "    make lint        Run ruff linter"
	@echo "    make format      Run ruff formatter"
	@echo "    make typecheck   Run mypy"
	@echo "    make test        Run pytest"
	@echo "    make web-check   Dashboard (web/): typecheck + lint + vitest + build"
	@echo "    make check       lint + typecheck + test (+ web-check when web/ and node exist)"
	@echo ""

# ── Run ───────────────────────────────────────────────────────────────────────

.PHONY: run
run:
	docker compose up -d && docker compose logs -f synatyx-mcp synatyx-gc

# ── Docker ────────────────────────────────────────────────────────────────────

.PHONY: up
up:
	docker compose up -d --build  # GIT_COMMIT exported above → baked into images

.PHONY: down
down:
	docker compose down

.PHONY: restart
restart: down up

.PHONY: logs
logs:
	docker compose logs -f synatyx-mcp synatyx-gc

.PHONY: migrate
migrate:
	docker compose run --rm migrate

.PHONY: build
build:
	docker compose build

# ── Local dev ─────────────────────────────────────────────────────────────────

.PHONY: install
install:
	$(UV) venv
	$(UV) pip install -e ".[dev]"

.PHONY: mcp
mcp:
	RUN_MODE=mcp $(PYTHON) main.py

.PHONY: dev
dev:
	RUN_MODE=graphql $(PYTHON) main.py

# ── Quality ───────────────────────────────────────────────────────────────────

.PHONY: lint
lint:
	$(PYTHON) -m ruff check .

.PHONY: format
format:
	$(PYTHON) -m ruff format .

.PHONY: typecheck
typecheck:
	$(PYTHON) -m mypy src

.PHONY: test
test:
	$(PYTHON) -m pytest

.PHONY: test-integration
test-integration:
	$(PYTHON) -m pytest tests/integration -v

.PHONY: check
check: lint typecheck test web-check-if-present

# ── Web dashboard (web/) ──────────────────────────────────────────────────────
# Node is only needed here and in the Dockerfile's build stage. `check` runs
# web-check only when web/ exists and node is on PATH, so Python-only machines
# keep a green `make check`.

WEB := web

.PHONY: web-install
web-install:
	@if [ ! -f $(WEB)/node_modules/.package-lock.json ] || [ $(WEB)/package-lock.json -nt $(WEB)/node_modules/.package-lock.json ]; then \
		cd $(WEB) && npm ci --no-audit --no-fund; \
	fi

.PHONY: web-check
web-check: web-install
	cd $(WEB) && npm run typecheck && npm run lint && npm run test && npm run build

.PHONY: web-check-if-present
web-check-if-present:
	@if [ -d $(WEB) ] && command -v node >/dev/null 2>&1; then \
		$(MAKE) --no-print-directory web-check; \
	else \
		echo "web-check skipped: no $(WEB)/ or no node on PATH"; \
	fi

.PHONY: web-e2e
web-e2e: web-install
	cd $(WEB) && npx playwright test

