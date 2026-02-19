# TenancyManager

`TenancyManager` is the central orchestrator.  It owns the lifecycle of all
other components: storage, resolver, isolation provider, and (via
`create_lifespan`) the FastAPI app's startup/shutdown sequence.

## Auto-reference

::: fastapi_tenancy.manager.TenancyManager
    options:
      show_source: true
      show_root_heading: true
      members_order: source
      filters: ["!^_"]

---

## Why no `app` parameter?

Earlier versions accepted `FastAPI` as the first argument to `__init__`.
This created a tight coupling: the manager could not be constructed until the
app existed, and could not be tested without a running app.

`v0.2.0` removes `app` from `__init__`.  The FastAPI app is only needed in
two places — both handled inside `create_lifespan`:

1. **Middleware registration** — `app.add_middleware(TenancyMiddleware, ...)`
2. **App-state storage** — `app.state.tenancy_manager = manager`

Both of these happen *before* the lifespan `yield`, which is the only point
where Starlette allows middleware changes.

## Middleware registration timing

FastAPI (Starlette) raises `RuntimeError: Cannot add middleware after an
application has started` if `add_middleware` is called after the application
has processed its first request.

`create_lifespan` always registers the middleware **before** `yield`:

```python
@asynccontextmanager
async def _lifespan(app: FastAPI):
    # ← BEFORE yield: middleware stack not yet frozen
    app.add_middleware(TenancyMiddleware, config=config, manager=manager)
    await manager.initialize()
    yield          # ← app is now serving; middleware stack is frozen here
    await manager.shutdown()
```

If you wire the middleware manually, you must follow the same rule:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.add_middleware(TenancyMiddleware, manager=manager)  # ← before yield!
    await manager.initialize()
    yield
    await manager.shutdown()
```

## Integration patterns

### Recommended — `create_lifespan`

```python
from fastapi import FastAPI
from fastapi_tenancy import TenancyConfig, TenancyManager

config = TenancyConfig(database_url="...", resolution_strategy="header",
                       isolation_strategy="schema")

app = FastAPI(lifespan=TenancyManager.create_lifespan(config))
```

### Custom store or resolver

```python
from fastapi_tenancy import TenancyManager, TenancyConfig, InMemoryTenantStore

config = TenancyConfig(...)
store = InMemoryTenantStore()    # or your own TenantStore subclass

app = FastAPI(
    lifespan=TenancyManager.create_lifespan(
        config,
        tenant_store=store,
    )
)
```

### Manual wiring (advanced)

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi_tenancy import TenancyManager, TenancyMiddleware, TenancyConfig

config = TenancyConfig(...)
manager = TenancyManager(config)

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.add_middleware(TenancyMiddleware, config=config, manager=manager)
    await manager.initialize()
    yield
    await manager.shutdown()

app = FastAPI(lifespan=lifespan)
```

### In tests — async context manager

```python
import pytest
from fastapi_tenancy import TenancyManager, TenancyConfig, InMemoryTenantStore

@pytest.fixture
async def manager():
    config = TenancyConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        resolution_strategy="header",
        isolation_strategy="schema",
    )
    store = InMemoryTenantStore()
    async with TenancyManager(config, tenant_store=store) as m:
        yield m
```

### Background tasks — `tenant_scope`

```python
from fastapi import BackgroundTasks

@app.post("/reports")
async def trigger_report(bg: BackgroundTasks, tenant=Depends(get_current_tenant)):
    manager: TenancyManager = request.app.state.tenancy_manager
    bg.add_task(generate_report, manager, tenant.id)

async def generate_report(manager: TenancyManager, tenant_id: str):
    async with manager.tenant_scope(tenant_id) as tenant:
        async with manager.isolation_provider.get_session(tenant) as session:
            # ... run queries in tenant's schema
            pass
```
