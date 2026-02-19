# TenantContext

`TenantContext` manages the **per-request** tenant using Python's
`contextvars.ContextVar` — the same mechanism used by `asyncio` task-local
storage.  Each async task (i.e., each HTTP request) gets its own isolated
copy of the tenant; there is no cross-request leakage.

## Auto-reference

::: fastapi_tenancy.core.context.TenantContext
    options:
      show_source: true
      show_root_heading: true
      members_order: source

::: fastapi_tenancy.core.context.get_current_tenant
    options:
      show_source: false

::: fastapi_tenancy.core.context.get_current_tenant_optional
    options:
      show_source: false

---

## How it works

`ContextVar` values are per-task.  When `asyncio` creates a new task (which
Starlette does for every HTTP request) it copies the current context, giving
each request its own isolated variable store:

```
Request A  →  task A  →  ContextVar["tenant"] = Tenant("acme-corp")
Request B  →  task B  →  ContextVar["tenant"] = Tenant("widgets-inc")
# A and B run concurrently; neither can see the other's tenant
```

`TenancyMiddleware` sets the context at the start of every request and clears
it in a `finally` block — guaranteeing cleanup even if the request handler
raises an exception.

## Usage in routes

```python
from fastapi_tenancy import get_current_tenant, Tenant

@app.get("/me")
async def whoami(tenant: Tenant = Depends(get_current_tenant)):
    return {"identifier": tenant.identifier}
```

## Usage in services / business logic

```python
from fastapi_tenancy import TenantContext

async def send_invoice(invoice_id: str) -> None:
    tenant = TenantContext.get()  # raises if no context set
    logger.info("Sending invoice %s for tenant %s", invoice_id, tenant.id)
    ...
```

## Usage in background tasks

Background tasks run in a **new** `asyncio` task — the context from the
request is **not** automatically propagated.  Use `manager.tenant_scope` or
`TenantContext.scope` explicitly:

```python
from fastapi_tenancy import TenantContext

async def background_job(tenant_id: str) -> None:
    store = ...  # get store somehow
    tenant = await store.get_by_id(tenant_id)

    async with TenantContext.scope(tenant):
        # context is set here — safe to call TenantContext.get()
        await do_work()
```

## Context metadata

Attach arbitrary key-value data to the current request context:

```python
TenantContext.set_metadata("request_id", "abc-123")
TenantContext.set_metadata("user_id", "user-456")

req_id = TenantContext.get_metadata("request_id")
all_meta = TenantContext.get_all_metadata()  # returns a copy
```

!!! warning "Default is `None`, not `{}`"
    The internal `ContextVar` for metadata defaults to `None` (not `{}`).
    Using `{}` as a default would share a single mutable dict across all
    tasks that never called `set_metadata`.  Always use `get_metadata(key,
    default)` or check `get_all_metadata()` before mutating.
