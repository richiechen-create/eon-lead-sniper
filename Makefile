# Local dev sandbox commands. Nothing here touches Neon or Render —
# everything operates on lead_engine_dev.sqlite + your .env.dev.

.PHONY: help dev seed reset test serve init

PYTHON := .venv/bin/python
PIP := .venv/bin/pip
UVICORN := .venv/bin/uvicorn
PYTEST := .venv/bin/pytest
ALEMBIC := .venv/bin/alembic

# Load .env.dev for all targets that boot the app/seed/test. If .env.dev
# doesn't exist, the .env.dev.example is copied in (one-shot bootstrap).
ENVFILE := .env.dev

help: ## Show this help
	@echo "Local dev commands:"
	@echo "  make init    - first-time setup: venv + deps + .env.dev + seed + serve"
	@echo "  make dev     - serve locally with auto-reload (uses .env.dev)"
	@echo "  make seed    - add sample data to lead_engine_dev.sqlite"
	@echo "  make reset   - wipe dev DB + re-seed from scratch"
	@echo "  make test    - run pytest"
	@echo

init: $(ENVFILE) ## First-time setup
	@if [ ! -d .venv ]; then echo "→ Creating venv"; uv venv --python 3.11; fi
	@echo "→ Installing deps"
	uv pip install -e ".[dev]"
	@$(MAKE) reset
	@$(MAKE) dev

$(ENVFILE):
	@echo "→ Creating $(ENVFILE) from .env.dev.example"
	cp .env.dev.example $(ENVFILE)
	@echo "  Edit $(ENVFILE) if you want to customise dev creds."

seed: $(ENVFILE) ## Add sample data to dev DB
	@set -a && . ./$(ENVFILE) && set +a && $(PYTHON) -m scripts.seed_dev

reset: $(ENVFILE) ## Wipe and re-seed dev DB
	@set -a && . ./$(ENVFILE) && set +a && $(PYTHON) -m scripts.seed_dev --wipe

dev: $(ENVFILE) ## Run the app locally with reload
	@set -a && . ./$(ENVFILE) && set +a && $(UVICORN) app.api.main:app --reload --host 127.0.0.1 --port 8000

serve: dev ## Alias for `make dev`

test: ## Run pytest (uses :memory: sqlite, not the dev DB)
	$(PYTEST)
