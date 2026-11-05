.PHONY: help
default: help

PYTHON ?= python3
export PYTHONPATH := src

help: ## Display this help screen
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'
install: ## Install dependencies
	$(PYTHON) -m pip install -e ".[dev]"
build: ## Build the library version
	$(PYTHON) -m build
serve: ## Run the web app on http://127.0.0.1:8000
	$(PYTHON) -m persephone.web --reload
seed: ## Fill the local library with demo data
	$(PYTHON) -m persephone.seed
covers: ## Save a local copy of every book cover (FORCE=1 to download all again)
	$(PYTHON) -m persephone.covers $(if $(FORCE),--force)
import: ## Import from info/
	$(PYTHON) -m persephone.importer info
export-data: ## Download your own data to a JSON file (OWNER=name to add yours)
	$(PYTHON) -m persephone.userdata export $(if $(OWNER),--owner "$(OWNER)")
import-data: ## Import your own data from a JSON file: make import-data FILE=...
	$(PYTHON) -m persephone.userdata import "$(FILE)"
test: ## Run the test suite
	$(PYTHON) -m pytest --cov
lint: ## Apply the linter
	ruff check --fix
