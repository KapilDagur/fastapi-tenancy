# Testing

fastapi-tenancy is designed with testability as a first-class concern.
Every component is swappable via Protocol/ABC, and `InMemoryTenantStore`
provides a fast, zero-dependency test double.

---

## Basic setup

```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from fastapi_tenancy import (
    TenancyConfig, TenancyManager, TenancyMiddleware,
    Tenant, TenantStatus,
)
from fastapi_tenancy.storage.memory import InMemoryTenantStore


@pytest.fixture
def store() -> InMemoryTenantStore:
    return InMemoryTenantStore()


@pytest.fixture
def config() -> TenancyConfig:
    return TenancyConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        resolution_strategy="header",
        isolation_strategy="rls",
    )


@pytest.fixture
async def seeded_store(store: InMemoryTenantStore) -> InMemoryTenantStore:
    tenant = Tenant(
        id="test-tenant-001",
        identifier="test-corp",
        name="Test Corporation",
        status=TenantStatus.ACTIVE,
    )
    await store.create(tenant)
    return store
```

---

## Testing with AsyncClient (recommended)

```python
import pytest
from httpx import AsyncClient, ASGITransport
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends

from fastapi_tenancy import get_current_tenant, Tenant


@pytest.fixture
async def app(config, seeded_store):
    manager = TenancyManager(config, tenant_store=seeded_store)

    @asynccontextmanager
    async def lifespan(app):
        app.add_middleware(TenancyMiddleware, config=config, manager=manager)
        await manager.initialize()
        yield
        await manager.shutdown()

    _app = FastAPI(lifespan=lifespan)

    @_app.get("/me")
    async def who_am_i(tenant: Tenant = Depends(get_current_tenant)):
        return {"identifier": tenant.identifier}

    return _app


@pytest.mark.asyncio
async def test_header_resolution(app):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # Valid tenant
        response = await client.get("/me", headers={"X-Tenant-ID": "test-corp"})
        assert response.status_code == 200
        assert response.json()["identifier"] == "test-corp"

        # Missing header
        response = await client.get("/me")
        assert response.status_code == 400
        assert response.json()["error"] == "tenant_resolution_failed"

        # Unknown tenant
        response = await client.get("/me", headers={"X-Tenant-ID": "unknown"})
        assert response.status_code == 404
```

---

## Testing tenant context in isolation

You can test context management without a full HTTP stack:

```python
import pytest
from fastapi_tenancy.core.context import TenantContext
from fastapi_tenancy.core.types import Tenant


@pytest.mark.asyncio
async def test_tenant_context_set_and_clear():
    tenant = Tenant(id="t1", identifier="test", name="Test")

    async with TenantContext.scope(tenant):
        assert TenantContext.get().id == "t1"
        assert TenantContext.get_optional() is not None

    # Context cleared after scope
    assert TenantContext.get_optional() is None


@pytest.mark.asyncio
async def test_context_cleared_on_exception():
    tenant = Tenant(id="t1", identifier="test", name="Test")

    with pytest.raises(ValueError):
        async with TenantContext.scope(tenant):
            raise ValueError("oops")

    assert TenantContext.get_optional() is None
```

---

## Concurrent isolation tests (critical correctness)

These tests verify the core guarantee: concurrent async tasks cannot see
each other's tenant context.

```python
import asyncio
import pytest
from fastapi_tenancy.core.context import TenantContext
from fastapi_tenancy.core.types import Tenant


@pytest.mark.asyncio
async def test_concurrent_tenant_isolation():
    """Two concurrent coroutines each see only their own tenant."""
    t1 = Tenant(id="t1-001", identifier="tenant-one", name="One")
    t2 = Tenant(id="t2-002", identifier="tenant-two", name="Two")
    results: dict[str, str] = {}

    async def run(tenant: Tenant) -> None:
        async with TenantContext.scope(tenant):
            await asyncio.sleep(0.01)  # other coroutine runs here
            results[tenant.id] = TenantContext.get().id

    await asyncio.gather(run(t1), run(t2))

    assert results == {t1.id: t1.id, t2.id: t2.id}


@pytest.mark.asyncio
async def test_metadata_not_shared_across_concurrent_tasks():
    t1 = Tenant(id="m1", identifier="meta-one", name="One")
    t2 = Tenant(id="m2", identifier="meta-two", name="Two")
    seen: dict[str, object] = {}

    async def run_t1():
        async with TenantContext.scope(t1):
            TenantContext.set_metadata("role", "admin")
            await asyncio.sleep(0.01)
            seen["t1_role"] = TenantContext.get_metadata("role")

    async def run_t2():
        async with TenantContext.scope(t2):
            await asyncio.sleep(0)
            seen["t2_role"] = TenantContext.get_metadata("role", "none")

    await asyncio.gather(run_t1(), run_t2())

    assert seen["t1_role"] == "admin"
    assert seen["t2_role"] == "none"   # t2 must not see t1's metadata
```

!!! note "Why these tests matter"
    The concurrent isolation tests encode the fundamental correctness guarantee
    of `TenantContext`. If `contextvars` ever leaks between async tasks, these
    tests catch it immediately.

---

## pytest markers

`pytest.ini` defines three markers:

```ini
[pytest]
markers =
    unit: Unit tests — no external services
    integration: Integration tests — require database
    e2e: End-to-end tests — require full stack (Docker Compose)
asyncio_mode = auto
```

```bash
# Run only unit tests (fastest)
pytest -m unit

# Run unit + integration
pytest -m "unit or integration"

# Run everything
pytest

# Coverage report
pytest --cov=src/fastapi_tenancy --cov-report=term-missing
```

---

## Custom isolation provider for tests

```python
from fastapi_tenancy.isolation.base import BaseIsolationProvider
from fastapi_tenancy.core.types import Tenant
from sqlalchemy.ext.asyncio import AsyncSession
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator


class NoOpIsolationProvider(BaseIsolationProvider):
    """Isolation provider that does nothing — for unit tests."""

    @asynccontextmanager
    async def get_session(self, tenant: Tenant) -> AsyncIterator[AsyncSession]:
        # Return a mock session or your test database session
        yield mock_session  # type: ignore

    async def apply_filters(self, query, tenant):
        return query

    async def initialize_tenant(self, tenant: Tenant) -> None:
        pass   # no-op

    async def destroy_tenant(self, tenant: Tenant) -> None:
        pass   # no-op


# Use in tests
manager = TenancyManager(
    config,
    tenant_store=InMemoryTenantStore(),
    isolation_provider=NoOpIsolationProvider(config),
)
```
