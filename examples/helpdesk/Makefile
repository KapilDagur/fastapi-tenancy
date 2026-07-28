.DEFAULT_GOAL := help
.PHONY: help install lint format format-fix type test qa run clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Create the isolated environment from the lockfile
	uv sync --all-groups --locked

lint:  ## Ruff lint
	uv run ruff check .

format:  ## Ruff format check (no writes)
	uv run ruff format --check .

format-fix:  ## Apply formatting
	uv run ruff format .

type:  ## mypy strict
	uv run mypy src tests

test:  ## Full suite (starts a PostgreSQL container)
	uv run pytest

qa: lint format type test  ## The gate: lint + format + type + test
	@echo "QA gate passed."

run:  ## Serve the API against $$HELPDESK_DATABASE_URL
	uv run uvicorn helpdesk.app:app --reload

clean:  ## Remove build and cache artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
