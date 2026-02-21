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
#																		 #
#   make build          Build wheel + sdist								 #
#   make clean          Remove all build / test artefacts				 #
##########################################################################

.PHONY: dev lint fmt type security check test build clean

##########
# Config #
##########
PYTEST        := python -m pytest

#####################
# Development setup #
#####################
dev:
	pip install -e ".[dev,postgres,sqlite,mysql,redis,jwt,migrations]"

###################
# Static analysis #
###################
lint:
	ruff check src tests
	ruff format --check src tests

fmt:
	ruff check --fix src tests
	ruff format src tests

type:
	mypy src/fastapi_tenancy

security:
	bandit -r src/fastapi_tenancy -ll -ii

check: lint type security

#########
# Tests #
#########
test:
	$(PYTEST) tests/unit/ --tb=short -v

#########
# Build #
#########
build:
	python -m build
	twine check dist/*

################
# Housekeeping #
################
clean:
	rm -rf dist build htmlcov coverage.xml test-results.txt .coverage
	rm -rf .pytest_cache .mypy_cache .ruff_cache .tox
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
