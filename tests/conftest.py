"""Shared pytest fixtures for fastapi-tenancy test suite.

Design philosophy
-----------------
- All fixtures that touch I/O use SQLite in-memory so the test suite runs
  without any external services (PostgreSQL, Redis, etc.).
- Fixtures are async where the SUT is async.
- Scope is kept at "function" by default to guarantee full isolation.
- Module-scoped fixtures are used only for expensive setup that is provably
  read-only across tests (e.g., read-only config objects).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from fastapi_tenancy.core.context import TenantContext
from fastapi_tenancy.core.types import Tenant, TenantStatus
from fastapi_tenancy.storage.memory import InMemoryTenantStore

# ---------------------------------------------------------------------------
# Event loop — one per test session (required by pytest-asyncio)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


# ---------------------------------------------------------------------------
# Context isolation — always clear TenantContext between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clear_tenant_context():
    """Guarantee TenantContext is empty before and after every test."""
    TenantContext.clear()
    yield
    TenantContext.clear()


# ---------------------------------------------------------------------------
# Tenant fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tenant_acme() -> Tenant:
    return Tenant(
        id="acme-001",
        identifier="acme-corp",
        name="Acme Corp",
        status=TenantStatus.ACTIVE,
        metadata={"plan": "enterprise", "region": "us-east-1"},
    )


@pytest.fixture
def tenant_widgets() -> Tenant:
    return Tenant(
        id="widgets-001",
        identifier="widgets-inc",
        name="Widgets Inc",
        status=TenantStatus.ACTIVE,
        metadata={"plan": "starter"},
    )


@pytest.fixture
def tenant_suspended() -> Tenant:
    return Tenant(
        id="suspended-001",
        identifier="suspended-corp",
        name="Suspended Corp",
        status=TenantStatus.SUSPENDED,
    )


@pytest.fixture
def tenant_provisioning() -> Tenant:
    return Tenant(
        id="prov-001",
        identifier="new-startup",
        name="New Startup",
        status=TenantStatus.PROVISIONING,
    )


@pytest.fixture
def all_tenants(tenant_acme, tenant_widgets, tenant_suspended, tenant_provisioning):
    return [tenant_acme, tenant_widgets, tenant_suspended, tenant_provisioning]


# ---------------------------------------------------------------------------
# Storage fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def memory_store() -> InMemoryTenantStore:
    return InMemoryTenantStore()


@pytest.fixture
async def populated_store(
    memory_store: InMemoryTenantStore,
    tenant_acme: Tenant,
    tenant_widgets: Tenant,
    tenant_suspended: Tenant,
) -> InMemoryTenantStore:
    """In-memory store pre-populated with three tenants."""
    await memory_store.create(tenant_acme)
    await memory_store.create(tenant_widgets)
    await memory_store.create(tenant_suspended)
    return memory_store


@pytest.fixture
async def sqlite_store():
    """SQLite-backed tenant store for integration tests."""
    from fastapi_tenancy.storage.postgres import SQLAlchemyTenantStore

    store = SQLAlchemyTenantStore(
        database_url="sqlite+aiosqlite:///:memory:",
        pool_size=1,
    )
    await store.initialize()
    yield store
    await store.close()


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pg_config():
    """TenancyConfig for PostgreSQL (for unit tests — engine not created)."""
    from fastapi_tenancy.core.config import TenancyConfig
    return TenancyConfig(
        database_url="postgresql+asyncpg://user:pass@localhost/testdb",
        resolution_strategy="header",
        isolation_strategy="rls",
    )


@pytest.fixture
def sqlite_config():
    """TenancyConfig for SQLite — usable without a real DB."""
    from fastapi_tenancy.core.config import TenancyConfig
    return TenancyConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        resolution_strategy="header",
        isolation_strategy="schema",
    )


# ---------------------------------------------------------------------------
# Resolver fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def header_resolver(populated_store: InMemoryTenantStore):
    from fastapi_tenancy.resolution.header import HeaderTenantResolver
    return HeaderTenantResolver(tenant_store=populated_store)


@pytest.fixture
def mock_resolver() -> MagicMock:
    """Resolver that always succeeds — swap .resolve side_effect per test."""
    r = MagicMock()
    r.resolve = AsyncMock()
    return r


# ---------------------------------------------------------------------------
# Middleware fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_app() -> MagicMock:
    app = AsyncMock()
    return app


@pytest.fixture
def middleware_with_resolver(mock_app, mock_resolver):
    from fastapi_tenancy.middleware.tenancy import TenancyMiddleware
    return TenancyMiddleware(app=mock_app, resolver=mock_resolver)


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------

def make_request(
    path: str = "/api/data",
    host: str = "localhost",
    headers: dict | None = None,
) -> MagicMock:
    req = MagicMock()
    req.url.path = path
    req.url.hostname = host
    req.headers = headers or {}
    req.state = MagicMock()
    return req
