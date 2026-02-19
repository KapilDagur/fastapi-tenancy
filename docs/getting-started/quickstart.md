# Quickstart

This guide walks you from installation to a fully working multi-tenant FastAPI
application with tenant provisioning, route protection, and database sessions.

## 1. Install

```bash
pip install "fastapi-tenancy[postgres]"
```

## 2. Configure

Create a `TenancyConfig`. All settings can also be set via environment
variables prefixed with `TENANCY_`.

```python
from fastapi_tenancy import TenancyConfig

config = TenancyConfig(
    # Required
    database_url="postgresql+asyncpg://user:pass@localhost/myapp",

    # How to identify which tenant owns a request
    resolution_strategy="header",    # reads X-Tenant-ID header

    # How to isolate each tenant's data
    isolation_strategy="schema",     # one PostgreSQL schema per tenant
)
```

!!! tip "Environment variables"
    ```bash
    # .env file or shell environment
    TENANCY_DATABASE_URL=postgresql+asyncpg://user:pass@localhost/myapp
    TENANCY_RESOLUTION_STRATEGY=header
    TENANCY_ISOLATION_STRATEGY=schema
    ```
    Then just: `config = TenancyConfig()`

## 3. Create the FastAPI app

```python
from fastapi import FastAPI
from fastapi_tenancy import TenancyConfig, TenancyManager

config = TenancyConfig(
    database_url="postgresql+asyncpg://user:pass@localhost/myapp",
    resolution_strategy="header",
    isolation_strategy="schema",
)

# create_lifespan does three things correctly:
#   1. Registers TenancyMiddleware BEFORE lifespan yields (required by FastAPI)
#   2. Calls manager.initialize() on startup
#   3. Calls manager.shutdown() on teardown
app = FastAPI(lifespan=TenancyManager.create_lifespan(config))
```

!!! warning "Middleware registration timing"
    FastAPI/Starlette raises `RuntimeError` if you call `app.add_middleware()`
    **after** startup. `create_lifespan` registers the middleware correctly —
    **before** the `yield` — so this is handled automatically.
    See the [Middleware & Lifespan](../guides/middleware.md) guide for the
    manual wiring pattern if you need it.

## 4. Use tenant context in routes

```python
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_tenancy import Tenant, get_current_tenant, get_tenant_db

@app.get("/me")
async def who_am_i(tenant: Tenant = Depends(get_current_tenant)):
    """Returns the resolved tenant for the current request."""
    return {
        "id": tenant.id,
        "identifier": tenant.identifier,
        "name": tenant.name,
        "plan": tenant.metadata.get("plan", "free"),
    }


@app.get("/users")
async def list_users(
    session: AsyncSession = Depends(get_tenant_db),
):
    """
    session already has search_path set to tenant's schema.
    No WHERE clause needed — isolation is transparent.
    """
    result = await session.execute(select(User))
    return result.scalars().all()
```

## 5. Provision tenants

```python
from fastapi import Request
from fastapi_tenancy import Tenant, TenantStatus, TenancyManager
import uuid

class TenantCreate(BaseModel):
    slug: str      # e.g. "acme-corp"
    name: str      # e.g. "Acme Corporation"
    plan: str = "free"

@app.post("/admin/tenants", status_code=201)
async def create_tenant(request: Request, body: TenantCreate):
    manager: TenancyManager = request.app.state.tenancy_manager

    tenant = Tenant(
        id=str(uuid.uuid4()),
        identifier=body.slug,
        name=body.name,
        status=TenantStatus.ACTIVE,
        metadata={"plan": body.plan},
    )

    # 1. Persist to tenant store
    created = await manager.tenant_store.create(tenant)

    # 2. Create the schema (or database) for this tenant
    await manager.isolation_provider.initialize_tenant(created)

    return created
```

## 6. Test it

```bash
# Start the app
uvicorn myapp:app --reload

# Resolve tenant from header
curl -H "X-Tenant-ID: acme-corp" http://localhost:8000/me
# {"id": "...", "identifier": "acme-corp", "name": "Acme Corp", "plan": "free"}

# Missing header → 400
curl http://localhost:8000/me
# {"error": "tenant_resolution_failed", "message": "Required tenant header not found"}

# Unknown tenant → 404
curl -H "X-Tenant-ID: doesnt-exist" http://localhost:8000/me
# {"error": "tenant_not_found", "message": "Tenant not found: doesnt-exist"}
```

## What happens on each request?

```
POST /api/orders
X-Tenant-ID: acme-corp

  TenancyMiddleware
  │
  ├─ 1. Skip check — not /health, not OPTIONS → proceed
  │
  ├─ 2. HeaderTenantResolver.resolve(request)
  │       reads header "X-Tenant-ID: acme-corp"
  │
  ├─ 3. TenantStore.get_by_identifier("acme-corp")
  │       SELECT * FROM tenants WHERE identifier = 'acme-corp'
  │
  ├─ 4. tenant.is_active() check → True
  │
  ├─ 5. TenantContext.set(tenant)      # stored in contextvars
  │
  └─ 6. call_next(request)
          │
          └─ get_tenant_db dependency
                │
                └─ SchemaIsolationProvider.get_session(tenant)
                        SET search_path TO tenant_acme_corp, public
                        yield session
                        # All ORM queries hit tenant's schema automatically

     finally: TenantContext.clear()    # always runs, even on exception
```
