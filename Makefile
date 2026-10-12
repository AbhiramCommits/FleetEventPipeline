PY ?= python3
NPM ?= npm
COMPOSE ?= docker compose
LOOKBACK_HOURS ?= 720
export

.PHONY: help up down api handler ingest elt dq report demo test fmt lint wait

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

up: ## Start PostgreSQL 16 in Docker
	$(COMPOSE) up -d

down: ## Stop and remove the Docker stack
	$(COMPOSE) down

api: ## Run the mock vendor API on :8099
	$(PY) -m uvicorn src.mockapi.app:app --host 0.0.0.0 --port 8099

handler: ## Run the Event Grid HTTP handler on :7099
	node handler/src/server.js

ingest: ## Run the incremental ingestion job
	$(PY) -m src.ingest.job --source all --lookback-hours $(LOOKBACK_HOURS)

elt: ## Build the mart star schema from staging
	$(PY) -m src.elt.run

dq: ## Run data-quality gates
	$(PY) -m src.dq.checks

report: ## Write reports/run_report.md + metrics.json
	$(PY) -m src.analysis.report

demo: ## End-to-end: migrate -> api -> handler -> ingest -> elt -> dq -> report
	bash scripts/demo.sh

test: ## Run pytest and node:test suites
	$(PY) -m pytest -q
	$(NPM) test

lint: ## Static checks (ruff + eslint)
	$(PY) -m ruff check src tests || true
	$(NPM) run lint --if-present

fmt: ## Auto-format Python and JS
	$(PY) -m ruff format src tests || true
	$(PY) -m ruff check --fix src tests || true
