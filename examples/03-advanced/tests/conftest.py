"""Shared pytest configuration for all Projectr test suites."""
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: end-to-end tests requiring docker compose up")


def pytest_collection_modifyitems(config, items):
    """Skip e2e tests unless -m e2e is explicitly passed."""
    skip_e2e = pytest.mark.skip(reason="E2E tests require docker compose up. Run: pytest -m e2e")
    for item in items:
        if "e2e" in item.keywords and not config.getoption("-m", default="").strip():
            item.add_marker(skip_e2e)
