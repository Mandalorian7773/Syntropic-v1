# SIH26117 sovereign AI workbench.
#
# Every target prints what it is doing. `make setup` on a fresh clone must
# succeed with nothing installed beyond Python 3.11, Node 20 and Docker.
#
# `make help` lists everything.

SHELL := /bin/bash
.DEFAULT_GOAL := help

ROOT    := $(shell pwd)
VENV    := $(ROOT)/.venv
# Override when your default python3 is not 3.11/3.12:
#     make setup PYTHON3=/usr/bin/python3.11
PYTHON3 ?= python3
PY      := $(VENV)/bin/python
PIP     := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn
PYTEST  := $(VENV)/bin/pytest

# Load .env if present so targets see MODEL_ENDPOINT and friends.
ifneq (,$(wildcard $(ROOT)/.env))
include $(ROOT)/.env
export
endif

.PHONY: help setup models types mock dev-front dev-back dev-rag demo collapse \
        airgap bench test clean

help:
	@echo "SIH26117 workbench"
	@echo
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-11s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------

setup: ## install contracts + both services + frontend deps, then run types
	@echo "==> setup [1/4] python venv"
	@# ragsvc pins python <3.13: rapidocr-onnxruntime 1.x refuses to install on
	@# 3.13, and rapidocr 3.x fetches its weights at first use, which breaks the
	@# air-gap. Fail here with a sentence rather than 200 lines of pip resolver
	@# output an hour later.
	@$(PYTHON3) -c 'import sys; v=sys.version_info; sys.exit(0 if (3,11) <= (v.major,v.minor) < (3,13) else 1)' \
	  || { echo "    ERROR: need Python 3.11 or 3.12, found $$($(PYTHON3) -V 2>&1)."; \
	       echo "    ragsvc's OCR backend does not build on 3.13. Install 3.11 and"; \
	       echo "    re-run, or point this at it:"; \
	       echo "        make setup PYTHON3=/path/to/python3.11"; \
	       exit 1; }
	@test -d $(VENV) || $(PYTHON3) -m venv $(VENV)
	@$(PIP) install --quiet --upgrade pip
	@echo "==> setup [2/4] contracts + backend + ragsvc (editable)"
	@# editable_mode=compat writes a plain .pth. Without it, running python from
	@# the repo root makes the bare ./contracts/ directory shadow the installed
	@# package as a PEP 420 namespace, and `from contracts import Token` fails.
	@$(PIP) install --quiet -e ./contracts --config-settings editable_mode=compat
	@$(PIP) install --quiet -e ./backend -e ./ragsvc
	@$(PIP) install --quiet pytest pytest-asyncio httpx
	@echo "==> setup [3/4] frontend deps"
	@cd frontend && npm install --no-audit --no-fund
	@echo "==> setup [4/4] generate types"
	@$(MAKE) --no-print-directory types
	@mkdir -p workspace artifacts data qdrant_data
	@echo "==> setup done. Next: cp .env.example .env && make dev-back"

models: ## read models/MANIFEST.yaml, download to ./models, verify SHA256
	@echo "==> models: reading models/MANIFEST.yaml"
	@PYTHON=$(PY) ./scripts/download-models.sh

types: ## regenerate frontend/src/types/events.ts from contracts
	@echo "==> types: contracts/ -> frontend/src/types/events.ts"
	@PYTHON=$(PY) ./scripts/gen-types.sh

# ---------------------------------------------------------------------------

mock: ## run frontend/mock/server.py alone
	@echo "==> mock: SSE server on :8000 (no venv needed, stdlib only)"
	@python3 frontend/mock/server.py

dev-front: ## vite dev server on 5173
	@echo "==> dev-front: vite on http://localhost:5173"
	@cd frontend && npm run dev

dev-back: ## uvicorn backend on 8000
	@echo "==> dev-back: uvicorn on http://localhost:8000 (GET /api/health)"
	@cd backend && $(UVICORN) main:app --host 0.0.0.0 --port 8000 --reload

dev-rag: ## uvicorn ragsvc on 8001 + qdrant container
	@echo "==> dev-rag: starting qdrant container"
	@docker start sih-qdrant 2>/dev/null \
	  || docker run -d --name sih-qdrant -p 6333:6333 \
	       -v $(ROOT)/qdrant_data:/qdrant/storage qdrant/qdrant:latest
	@echo "==> dev-rag: uvicorn on http://localhost:8001 (GET /health)"
	@cd ragsvc && $(UVICORN) main:app --host 0.0.0.0 --port 8001 --reload

# ---------------------------------------------------------------------------

demo: ## docker compose up, single host, internal network
	@echo "==> demo: bringing up the full stack on an internal network"
	@docker compose up --build -d
	@docker compose ps
	@echo "==> demo: UI on http://localhost:5173  --  now run 'make airgap'"

collapse: ## entire stack on this machine only, then run airgap
	@echo "==> collapse: stopping any LAN dev stack"
	@docker compose -f docker-compose.dev.yml down 2>/dev/null || true
	@test -f .env || cp .env.example .env
	@echo "==> collapse: pointing every endpoint at localhost"
	@sed -i.bak -E \
	  -e 's|^MODEL_ENDPOINT=.*|MODEL_ENDPOINT=http://localhost:8080|' \
	  -e 's|^RAG_ENDPOINT=.*|RAG_ENDPOINT=http://localhost:8001|' \
	  -e 's|^QDRANT_URL=.*|QDRANT_URL=http://localhost:6333|' .env && rm -f .env.bak
	@grep -E '^(MODEL_ENDPOINT|RAG_ENDPOINT|QDRANT_URL)=' .env | sed 's/^/    /'
	@$(MAKE) --no-print-directory demo
	@$(MAKE) --no-print-directory airgap

airgap: ## run scripts/airgap-check.sh, exit nonzero on failure
	@echo "==> airgap: proving there is no route out"
	@./scripts/airgap-check.sh

bench: ## run bench/run.py, write to bench/results/
	@echo "==> bench: replaying bench/tasks.jsonl"
	@$(PY) bench/run.py

test: ## pytest backend ragsvc + vitest frontend
	@echo "==> test [1/2] pytest"
	@# pytest exits 5 when it collects nothing. Empty test dirs are the expected
	@# state on day one, so 5 is a pass here, not a failure.
	@$(PYTEST) contracts/tests backend/tests ragsvc/tests -q; \
	  code=$$?; [ $$code -eq 0 ] || [ $$code -eq 5 ] || exit $$code
	@echo "==> test [2/2] frontend typecheck + vitest"
	@cd frontend && npm run typecheck && npm test

clean: ## remove build artifacts, keep models
	@echo "==> clean: removing build artifacts (models/ untouched)"
	@rm -rf frontend/dist frontend/.vite
	@find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@find . -name '*.egg-info' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache
	@echo "==> clean: done. 'make setup' rebuilds. Weights are still in ./models"
