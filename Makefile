.PHONY: help install dev-install test test-sqlite test-coverage lint format typecheck clean build docker-up docker-down docs

PYTHON   := python3
UV       := uv
SRC      := src
TESTS    := tests

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Installation ──────────────────────────────────────────────────────────────
install:  ## Install package (production deps only)
	$(UV) pip install -e .

dev-install:  ## Install with all dev + SQLite deps
	$(UV) pip install -e ".[dev,sqlite,postgres,redis,migrations]"

# ── Tests ─────────────────────────────────────────────────────────────────────
test:  ## Run full test suite
	pytest $(TESTS) -q

test-sqlite:  ## Run tests without external services (SQLite only)
	pytest $(TESTS) -m "not e2e" -q

test-coverage:  ## Run tests with coverage report
	pytest $(TESTS) -m "not e2e" --cov --cov-report=term-missing --cov-report=html -q
	@echo "\nHTML coverage report: htmlcov/index.html"

test-watch:  ## Re-run tests on file change (requires pytest-watch)
	ptw -- -m "not e2e" -q

# ── Code quality ──────────────────────────────────────────────────────────────
lint:  ## Lint with ruff
	ruff check $(SRC) $(TESTS)

format:  ## Format with ruff
	ruff format $(SRC) $(TESTS)
	ruff check --fix $(SRC) $(TESTS)

typecheck:  ## Type-check with mypy
	mypy $(SRC) --ignore-missing-imports

check: lint typecheck test-sqlite  ## Run lint + typecheck + sqlite tests

# ── Build ─────────────────────────────────────────────────────────────────────
clean:  ## Remove build artefacts
	rm -rf dist build *.egg-info .coverage htmlcov .pytest_cache .mypy_cache .ruff_cache

build: clean  ## Build wheel + sdist
	$(PYTHON) -m build

# ── Docker services ───────────────────────────────────────────────────────────
docker-up:  ## Start PostgreSQL + Redis
	docker compose up -d
	@echo "Waiting for services…"
	@sleep 3
	@docker compose ps

docker-down:  ## Stop and remove containers
	docker compose down -v

docker-logs:  ## Tail service logs
	docker compose logs -f

# ── Tox ───────────────────────────────────────────────────────────────────────
tox:  ## Run full tox matrix
	tox

tox-sqlite:  ## Run tox SQLite envs only
	tox -e py311-sqlite,py312-sqlite,py313-sqlite

tox-lint:  ## Run tox lint + type env
	tox -e lint,type

# ── Security ──────────────────────────────────────────────────────────────────
audit:  ## Check for dependency vulnerabilities
	pip-audit

# ── pre-commit ────────────────────────────────────────────────────────────────
hooks-install:  ## Install pre-commit hooks
	pre-commit install

hooks-run:  ## Run all pre-commit hooks manually
	pre-commit run --all-files
