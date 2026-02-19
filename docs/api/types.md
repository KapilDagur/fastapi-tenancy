# Types

Core domain types used throughout the library.

## Auto-reference

::: fastapi_tenancy.core.types
    options:
      show_source: true
      show_root_heading: false
      members_order: source
      filters: ["!^_"]

---

## `Tenant` — immutable domain model

`Tenant` is a **frozen** Pydantic v2 model.  Once created it cannot be
mutated — any "update" returns a new instance via `model_copy(update={...})`.
This prevents accidental shared-state bugs in async code.

```python
from fastapi_tenancy import Tenant, TenantStatus

tenant = Tenant(
    id="t-acme-001",
    identifier="acme-corp",
    name="Acme Corporation",
    status=TenantStatus.ACTIVE,
    metadata={"plan": "enterprise", "max_users": 500},
)

# Immutable — raises ValidationError
tenant.name = "New Name"  # TypeError: "Tenant" is immutable

# Correct way to produce an updated copy
updated = tenant.model_copy(update={"name": "Acme Corp Ltd"})
```

### Helper methods

```python
tenant.is_active()         # True if status == ACTIVE
tenant.is_suspended()      # True if status == SUSPENDED
tenant.get_metadata("plan", default="free")  # safe metadata access
```

## `TenantStatus` enum

| Value | Meaning |
|-------|---------|
| `ACTIVE` | Tenant can access the system |
| `SUSPENDED` | Blocked — middleware returns 403 |
| `PENDING` | Not yet activated |
| `DELETED` | Soft-deleted; data may still exist |

## `IsolationStrategy` enum

| Value | Provider |
|-------|----------|
| `SCHEMA` | `SchemaIsolationProvider` |
| `DATABASE` | `DatabaseIsolationProvider` |
| `RLS` | `RLSIsolationProvider` |
| `HYBRID` | `HybridIsolationProvider` |

## `ResolutionStrategy` enum

| Value | Resolver |
|-------|----------|
| `HEADER` | `HeaderTenantResolver` |
| `SUBDOMAIN` | `SubdomainTenantResolver` |
| `PATH` | `PathTenantResolver` |
| `JWT` | `JWTTenantResolver` |

## `BaseIsolationProvider` — ABC + Protocol

`BaseIsolationProvider` is both an abstract base class (enforced by `ABC`)
and a `runtime_checkable Protocol`.  This means:

```python
from fastapi_tenancy import BaseIsolationProvider

# ABC enforcement — cannot instantiate without implementing abstracts
class Incomplete(BaseIsolationProvider):
    pass  # TypeError at instantiation

# Protocol check — works on third-party objects that duck-type the interface
isinstance(my_provider, BaseIsolationProvider)  # True if it has get_session etc.
```

## `BaseTenantResolver` — ABC

All resolvers must implement `resolve(request) -> Tenant`.

```python
from fastapi_tenancy import BaseTenantResolver
from fastapi import Request

class MyResolver(BaseTenantResolver):
    async def resolve(self, request: Request) -> Tenant:
        ...
```

## `TenantConfig` — per-tenant settings

A lightweight Pydantic model hydrated from `tenant.metadata`:

```python
from fastapi_tenancy import get_tenant_config, TenantConfig
from fastapi import Depends

@app.get("/limits")
async def limits(cfg: TenantConfig = Depends(get_tenant_config)):
    return {
        "max_users": cfg.max_users,
        "rate_limit": cfg.rate_limit_per_minute,
        "features": cfg.features_enabled,
    }
```
