# API Reference

Complete reference for all public classes, functions, and types in
`fastapi-tenancy`.  Generated from docstrings via
[mkdocstrings](https://mkdocstrings.github.io/).

## Module structure

```
fastapi_tenancy/
├── __init__.py          # Public re-exports — start here
├── core/
│   ├── config.py        # TenancyConfig (Pydantic Settings)
│   ├── context.py       # TenantContext, get_current_tenant
│   ├── exceptions.py    # Exception hierarchy
│   └── types.py         # Tenant, TenantStatus, enums, ABCs
├── manager.py           # TenancyManager — lifecycle orchestrator
├── middleware/
│   └── tenancy.py       # TenancyMiddleware (Starlette BaseHTTPMiddleware)
├── dependencies.py      # FastAPI Depends helpers
├── storage/
│   ├── tenant_store.py  # TenantStore ABC
│   ├── memory.py        # InMemoryTenantStore (testing)
│   ├── postgres.py      # SQLAlchemyTenantStore (production)
│   └── redis.py         # RedisTenantStore (write-through cache)
├── isolation/
│   ├── base.py          # BaseIsolationProvider ABC
│   ├── schema.py        # SchemaIsolationProvider
│   ├── database.py      # DatabaseIsolationProvider
│   ├── rls.py           # RLSIsolationProvider
│   ├── hybrid.py        # HybridIsolationProvider
│   └── factory.py       # IsolationProviderFactory
├── resolution/
│   ├── base.py          # BaseTenantResolver ABC
│   ├── header.py        # HeaderTenantResolver
│   ├── subdomain.py     # SubdomainTenantResolver
│   ├── path.py          # PathTenantResolver
│   ├── jwt.py           # JWTTenantResolver
│   └── factory.py       # ResolverFactory
├── cache/
│   └── tenant_cache.py  # TenantCache (Redis)
└── migrations/
    └── manager.py       # MigrationManager (Alembic)
```

## What to import from where

Always import from the top-level package:

```python
from fastapi_tenancy import (
    TenancyConfig,
    TenancyManager,
    TenancyMiddleware,
    TenantContext,
    get_current_tenant,
    get_tenant_db,
    Tenant,
    TenantStatus,
    TenantStore,
    InMemoryTenantStore,
    SQLAlchemyTenantStore,
    TenantCache,
    MigrationManager,
    # Exceptions
    TenancyError,
    TenantNotFoundError,
    TenantResolutionError,
    TenantInactiveError,
    IsolationError,
)
```

Importing from submodules directly is supported but not guaranteed to remain
stable across minor versions.

## Section overview

| Page | Contents |
|------|----------|
| [TenancyConfig](config.md) | All configuration fields, validators, env-var mapping |
| [TenancyManager](manager.md) | Lifecycle, `create_lifespan`, `tenant_scope`, `health_check` |
| [TenancyMiddleware](middleware.md) | Skip-path logic, resolver delegation, error responses |
| [TenantContext](context.md) | `set`, `get`, `get_optional`, `scope`, `set_metadata` |
| [Storage](storage.md) | `TenantStore` ABC, `SQLAlchemyTenantStore`, `InMemoryTenantStore`, `RedisTenantStore` |
| [Isolation](isolation.md) | `BaseIsolationProvider`, all four concrete providers, factory |
| [Resolution](resolution.md) | `BaseTenantResolver`, all four concrete resolvers, factory |
| [Exceptions](exceptions.md) | Full exception hierarchy with fields and usage |
| [Types](types.md) | `Tenant`, `TenantStatus`, `TenantConfig`, `IsolationStrategy`, `ResolutionStrategy` |
