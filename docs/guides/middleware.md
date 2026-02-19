# Middleware & Lifespan

This guide covers `TenancyMiddleware` registration, the FastAPI lifespan
pattern, and why middleware timing matters.

## The golden rule

!!! danger "Middleware must be registered BEFORE the lifespan `yield`"
    FastAPI (Starlette) freezes the middleware stack the moment the first
    request arrives.  Calling `app.add_middleware` after `yield` raises:

    ```
    RuntimeError: Cannot add middleware after an application has started
    ```

    Always register `TenancyMiddleware` **before** `yield` in your lifespan.

## Option 1 — `create_lifespan` (recommended)

One line, zero boilerplate:

```python
from fastapi import FastAPI
from fastapi_tenancy import TenancyConfig, TenancyManager

config = TenancyConfig(
    database_url="postgresql+asyncpg://user:pass@localhost/myapp",
    resolution_strategy="header",
    isolation_strategy="schema",
)

app = FastAPI(lifespan=TenancyManager.create_lifespan(config))
```

Internally this generates a lifespan that:

```python
@asynccontextmanager
async def _lifespan(app: FastAPI):
    manager = TenancyManager(config)

    # ← BEFORE yield: middleware registration is still allowed here
    app.add_middleware(TenancyMiddleware, config=config, manager=manager)
    app.state.tenancy_manager = manager
    app.state.tenancy_config = config

    await manager.initialize()         # ← all I/O happens here

    app.state.tenant_store = manager.tenant_store
    app.state.isolation_provider = manager.isolation_provider

    yield                              # ← app now serving requests

    await manager.shutdown()           # ← cleanup
```

## Option 2 — manual wiring

Use this when you need a custom lifespan that does other things:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi_tenancy import TenancyConfig, TenancyManager, TenancyMiddleware

config = TenancyConfig(...)
manager = TenancyManager(config)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ← register BEFORE yield — mandatory
    app.add_middleware(
        TenancyMiddleware,
        config=config,
        manager=manager,
        skip_paths=["/health", "/internal"],
        debug_headers=False,
    )

    # your other startup logic
    await init_cache()
    await manager.initialize()

    yield

    await manager.shutdown()
    await close_cache()

app = FastAPI(lifespan=lifespan)
```

## Combining with other middleware

Middleware executes in **reverse registration order** (the last registered
runs first).  Register `TenancyMiddleware` early so it runs before
application-specific middleware that may need the tenant context:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.add_middleware(TenancyMiddleware, ...)          # runs first (outermost)
    app.add_middleware(RequestIDMiddleware)              # runs second
    app.add_middleware(RateLimitMiddleware)              # runs third
    await manager.initialize()
    yield
    await manager.shutdown()
```

## Accessing manager and store from routes

After `create_lifespan` or manual wiring, components are available via
`app.state`:

```python
from fastapi import Request
from fastapi_tenancy import TenancyManager

@app.post("/admin/tenants/{id}/suspend")
async def suspend_tenant(id: str, request: Request):
    manager: TenancyManager = request.app.state.tenancy_manager
    tenant = await manager.tenant_store.get_by_id(id)
    updated = tenant.model_copy(update={"status": TenantStatus.SUSPENDED})
    await manager.tenant_store.update(updated)
    return {"status": "suspended"}
```

## Customising skip paths

Skip paths bypass tenant resolution entirely — no header/JWT/subdomain check
is performed:

```python
TenancyManager.create_lifespan(
    config,
    skip_paths=[
        "/health",
        "/metrics",
        "/internal",     # internal monitoring endpoints
        "/_admin",       # ops tools that use a different auth
        "/webhook",      # inbound webhooks that use their own auth
    ],
)
```

## Debug headers

Enable during development to see which tenant was resolved for each response:

```python
TenancyManager.create_lifespan(config, debug_headers=True)
```

Adds to every response:

```
X-Tenant-ID: t-acme-001
X-Tenant-Identifier: acme-corp
```

!!! warning "Never enable `debug_headers` in production"
    Debug headers expose internal IDs to clients.  Gate on an environment
    variable: `debug_headers=(os.environ.get("ENV") == "development")`
