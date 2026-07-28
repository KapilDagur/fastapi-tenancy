##########################################################################
# Makefile — fastapi-tenancy developer workflow							 #
#------------------------------------------------------------------------#
#																		 #
# Quick reference														 #
#------------------------------------------------------------------------#
#   make dev            Install package in editable mode with all extras #
#   make lint           ruff check + ruff format						 #
#   make fmt            Auto-fix formatting with ruff					 #
#   make type           mypy --strict									 #
#   make security       bandit SAST scan								 #
#   make check          lint + type + security							 #
#																		 #
#   make test           Unit tests only									 #
#   make test-int       Integration tests (containers start on demand)   #
#   make test-e2e       End-to-end tests (containers start on demand)    #
#   make test-all       Full suite (containers start on demand)			 #
#   make coverage       Full suite → HTML report in htmlcov/			 #
#																		 #
#																		 #
#   make build          Build wheel + sdist								 #
#   make clean          Remove all build / test artefacts				 #
#																 #
#   Service containers are started on demand by the suite via		 #
#   Testcontainers (ADR 0003).  Nothing to start or tear down by	 #
#   hand - Docker just has to be running.							 #
##########################################################################

.PHONY: dev lint fmt type security check \
        test test-int test-e2e test-all coverage \
        build clean

##########
# Config #
##########
PYTEST        := python -m pytest
COV_FLAGS     := --cov=fastapi_tenancy \
                 --cov-report=term-missing \
                 --cov-report=html:htmlcov \
                 --cov-report=xml:coverage.xml
PYTHON_VER	  := 3.12


#####################
# Development setup #
#####################
dev:
	uv sync --all-extras --locked --python $(PYTHON_VER)

###################
# Static analysis #
###################
lint:
	uv run ruff check src tests
	uv run ruff format --check src tests
	echo "Code Linted Successfully"

fmt:
	uv run ruff check --fix src tests
	uv run ruff format src tests

type:
	uv run mypy src tests

security:
	uv run bandit -r src/fastapi_tenancy -ll -ii

check: lint type security

#########
# Tests #
#########
test:
	uv run $(PYTEST) -m unit tests/ --tb=short -v

test-int:
	uv run $(PYTEST) -m integration tests/ --tb=short -v $(COV_FLAGS)

test-e2e:
	uv run $(PYTEST) -m e2e tests/ --tb=short -v $(COV_FLAGS)

test-all:
	uv run $(PYTEST) tests/ --tb=short -v $(COV_FLAGS) 2>&1 | tee test-results.txt

coverage:
	uv run $(PYTEST) tests/ --tb=short $(COV_FLAGS)
	@echo ""
	@echo "HTML report → htmlcov/index.html"

#########
# Build #
#########
build:
	uv build

################
# Housekeeping #
################
clean:
	rm -rf dist build htmlcov coverage.xml test-results.txt .coverage
	rm -rf .pytest_cache .mypy_cache .ruff_cache .tox
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
