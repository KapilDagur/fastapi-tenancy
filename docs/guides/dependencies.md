# FastAPI Dependencies

fastapi-tenancy provides several FastAPI dependency functions that you use
with `Depends()` in your route handlers.

---

## `get_current_tenant`

Returns the `Tenant` resolved for the current request. Raises
`TenantNotFoundError` (→ 500) if called outside a tenant context.

```python
from fastapi import Depends
from fastapi_tenancy import Tenant, get_current_tenant

@app.get("/profile")
async def my_profile(tenant: Tenant = Depends(get_current_tenant)):
    return {
        "id": tenant.id,
        "name": tenant.name,
        "plan": tenant.metadata.get("plan", "free"),
    }
```

---

## `get_current_tenant_optional`

Returns the `Tenant` or `None`. Use for endpoints that work both with and
without a tenant context (e.g. public + tenant-aware routes).

```python
from fastapi_tenancy import get_current_tenant_optional

@app.get("/stats")
async def stats(tenant: Tenant | None = Depends(get_current_tenant_optional)):
    if tenant:
        return get_tenant_stats(tenant)
    return get_global_stats()
```

---

## `get_tenant_db`

Yields a `AsyncSession` scoped to the current tenant.
The session's `search_path` (or equivalent) is configured automatically
by the isolation provider.

```python
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi_tenancy import get_tenant_db

@app.get("/orders")
async def list_orders(session: AsyncSession = Depends(get_tenant_db)):
    result = await session.execute(select(Order))
    return result.scalars().all()
```

!!! note "Active-tenant check"
    `get_tenant_db` does **not** re-check `tenant.is_active()`. The middleware
    already performs this check before the request reaches any handler.
    The double-check was removed in v0.2 as dead code.

---

## `require_active_tenant`

Returns the tenant only if it's `ACTIVE`. Raises HTTP 403 for inactive tenants.

Use this for routes that bypass the standard middleware (webhooks,
admin routes with custom skip-path configuration):

```python
from fastapi_tenancy import require_active_tenant

@app.post("/webhook/stripe")
async def stripe_webhook(
    tenant: Tenant = Depends(require_active_tenant),
    payload: dict = Body(...),
):
    # tenant is guaranteed ACTIVE here
    await process_payment(tenant, payload)
```

---

## `get_tenant_config`

Returns a `TenantConfig` hydrated from the tenant's metadata blob.

```python
from fastapi_tenancy import TenantConfig, get_tenant_config

@app.get("/limits")
async def my_limits(cfg: TenantConfig = Depends(get_tenant_config)):
    return {
        "max_users": cfg.max_users,
        "features": cfg.features_enabled,
        "rate_limit": cfg.rate_limit_per_minute,
    }
```

`TenantConfig` fields are pulled from `tenant.metadata`:

| Field | Metadata key | Default |
|---|---|---|
| `max_users` | `max_users` | `None` |
| `max_storage_gb` | `max_storage_gb` | `None` |
| `features_enabled` | `features_enabled` | `[]` |
| `rate_limit_per_minute` | `rate_limit_per_minute` | `100` |
| `custom_settings` | `custom_settings` | `{}` |

---

## Combining dependencies

```python
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi_tenancy import Tenant, TenantConfig, get_current_tenant, get_tenant_db, get_tenant_config

@app.post("/users")
async def create_user(
    body: UserCreate,
    tenant: Tenant = Depends(get_current_tenant),
    cfg: TenantConfig = Depends(get_tenant_config),
    session: AsyncSession = Depends(get_tenant_db),
):
    # Check quota
    current_count = await session.scalar(select(func.count()).select_from(User))
    if cfg.max_users and current_count >= cfg.max_users:
        raise HTTPException(429, detail=f"User limit {cfg.max_users} reached")

    user = User(tenant_id=tenant.id, **body.model_dump())
    session.add(user)
    await session.commit()
    return user
```

---

## Custom dependencies

Build your own dependencies on top of the built-ins:

```python
from fastapi import Depends, HTTPException
from fastapi_tenancy import Tenant, get_current_tenant

async def require_enterprise_plan(
    tenant: Tenant = Depends(get_current_tenant),
) -> Tenant:
    """Only allow enterprise plan tenants."""
    plan = tenant.metadata.get("plan", "free")
    if plan != "enterprise":
        raise HTTPException(
            status_code=403,
            detail=f"This feature requires the Enterprise plan (current: {plan})",
        )
    return tenant


@app.get("/advanced-analytics")
async def advanced_analytics(
    tenant: Tenant = Depends(require_enterprise_plan),
):
    return await compute_advanced_analytics(tenant)
```
