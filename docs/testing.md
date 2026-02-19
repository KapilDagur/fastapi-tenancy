# Testing Guide

fastapi-tenancy ships a full test suite that runs with **no external services** using SQLite. This page explains how to write tests for applications built with the library.

---

## Running the library's own tests

```bash
# Fast (SQLite only, ~2 seconds)
pytest -m "not e2e"

# Full suite
pytest

# With coverage
pytest --cov --cov-report=term-missing
```

---

## Writing tests for your app

### Minimal fixture setup

```python
# conftest.py
import pytest
from fastapi_tenancy.core.context import TenantContext
from fastapi_tenancy.core.types import Tenant
from fastapi_tenancy.storage.memory import InMemoryTenantStore

@pytest.fixture(autouse=True)
def clear_context():
    TenantContext.clear()
    yield
    TenantContext.clear()

@pytest.fixture
def store():
    return InMemoryTenantStore()

@pytest.fixture
def acme():
    return Tenant(id="acme-001", identifier="acme-corp", name="Acme Corp")
```

### Testing with a real async SQLite store

```python
@pytest.fixture
async def sqlite_store():
    from fastapi_tenancy import SQLAlchemyTenantStore
    store = SQLAlchemyTenantStore(
        database_url="sqlite+aiosqlite:///:memory:", pool_size=1
    )
    await store.initialize()
    yield store
    await store.close()
```

!!! tip
    `SQLAlchemyTenantStore` is the canonical class.  `PostgreSQLTenantStore`
    is a deprecated alias that emits `DeprecationWarning` — update any
    existing imports.

### Testing isolation providers

```python
from unittest.mock import MagicMock, patch
from fastapi_tenancy.isolation.schema import SchemaIsolationProvider

def make_config(url="sqlite+aiosqlite:///:memory:"):
    cfg = MagicMock()
    cfg.database_url = url
    cfg.database_pool_size = 1
    cfg.database_max_overflow = 0
    cfg.database_pool_timeout = 5
    cfg.database_pool_recycle = 600
    cfg.database_echo = False
    cfg.database_url_template = None
    cfg.schema_prefix = "tenant_"
    cfg.get_schema_name = lambda tid: f"tenant_{tid.replace('-','_')}"
    return cfg

@pytest.mark.asyncio
async def test_schema_prefix_mode():
    with patch("fastapi_tenancy.isolation.schema.create_async_engine", return_value=MagicMock()):
        provider = SchemaIsolationProvider(make_config())
    tenant = Tenant(id="t1", identifier="acme-corp", name="Acme")
    assert provider.get_table_prefix(tenant) == "t_acme_corp_"
```

### Testing the middleware

```python
from unittest.mock import AsyncMock, MagicMock
from fastapi_tenancy.middleware.tenancy import TenancyMiddleware

@pytest.mark.asyncio
async def test_skip_health_check():
    m = TenancyMiddleware(app=MagicMock(), skip_paths=["/health"])
    call_next = AsyncMock(return_value=MagicMock(status_code=200))
    request = MagicMock()
    request.url.path = "/health"
    request.method = "GET"
    await m.dispatch(request, call_next)
    call_next.assert_called_once()
```

### Testing concurrent context isolation

```python
import asyncio

@pytest.mark.asyncio
async def test_concurrent_isolation():
    results = {}
    async def run(slug: str):
        t = Tenant(id=slug, identifier=slug, name=slug)
        async with TenantContext.scope(t):
            await asyncio.sleep(0.005)
            results[slug] = TenantContext.get().id
    await asyncio.gather(run("a"), run("b"), run("c"))
    assert results == {"a": "a", "b": "b", "c": "c"}
```

---

## Coverage tips

The library sets `fail_under = 80` in `pyproject.toml`. To exclude untestable lines:

```python
# pragma: no cover  ← skips a single line
if TYPE_CHECKING:    # automatically excluded by ruff/coverage config
    ...
```

Low-coverage areas to focus on: `isolation/database.py` (requires live DB), `isolation/hybrid.py` (needs mocked strategies), `cache/tenant_cache.py` (needs Redis mock).
