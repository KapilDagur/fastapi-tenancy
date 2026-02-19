# Isolation

Isolation providers determine how tenant data is physically separated in the
database.  The choice affects security, operational complexity, performance,
and cost.

## Class hierarchy

```
BaseIsolationProvider (ABC)
├── SchemaIsolationProvider    # one PostgreSQL schema per tenant
├── DatabaseIsolationProvider  # one database per tenant
├── RLSIsolationProvider       # shared tables + Row-Level Security
└── HybridIsolationProvider    # routes by tenant tier
```

## Strategy comparison

| Strategy | Isolation | Complexity | Scale | Best for |
|----------|-----------|-----------|-------|----------|
| Schema | Strong | Medium | Thousands | Mid-market SaaS |
| Database | Strongest | High | Hundreds | Enterprise / compliance |
| RLS | Moderate | Low | Millions | SMB / high-volume |
| Hybrid | Configurable | High | Mixed | Tiered pricing |

## BaseIsolationProvider

::: fastapi_tenancy.isolation.base.BaseIsolationProvider
    options:
      show_source: true
      show_root_heading: true
      members_order: source
      filters: ["!^_"]

---

## SchemaIsolationProvider

::: fastapi_tenancy.isolation.schema.SchemaIsolationProvider
    options:
      show_source: false
      show_root_heading: true
      members_order: source
      filters: ["!^_"]

### Dialect compatibility

| Dialect | Behaviour |
|---------|-----------|
| PostgreSQL | `CREATE SCHEMA "tenant_<slug>"` + `SET search_path TO …` |
| SQLite | Table-name prefix (`tenant_<slug>_<table>`) |
| MySQL | Delegates to `DatabaseIsolationProvider` (SCHEMA = DATABASE) |
| MSSQL | `CREATE SCHEMA` + connection-level schema switch |

---

## DatabaseIsolationProvider

::: fastapi_tenancy.isolation.database.DatabaseIsolationProvider
    options:
      show_source: false
      show_root_heading: true
      members_order: source
      filters: ["!^_"]

---

## RLSIsolationProvider

::: fastapi_tenancy.isolation.rls.RLSIsolationProvider
    options:
      show_source: false
      show_root_heading: true
      members_order: source
      filters: ["!^_"]

### `destroy_tenant` requires table information

Unlike schema/database strategies (where the entire namespace is dropped),
RLS shares tables with all tenants.  `destroy_tenant` therefore needs to know
which tables contain a `tenant_id` column so it can issue `DELETE` statements:

```python
# Option 1 — explicit table list
await provider.destroy_tenant(tenant, table_names=["users", "orders", "invoices"])

# Option 2 — SQLAlchemy metadata (recommended; auto-discovers tables)
from myapp.models import Base
await provider.destroy_tenant(tenant, metadata=Base.metadata)
```

Calling `destroy_tenant` without either argument raises `IsolationError`.

---

## HybridIsolationProvider

::: fastapi_tenancy.isolation.hybrid.HybridIsolationProvider
    options:
      show_source: false
      show_root_heading: true
      members_order: source
      filters: ["!^_"]

---

## IsolationProviderFactory

::: fastapi_tenancy.isolation.factory.IsolationProviderFactory
    options:
      show_source: false
      show_root_heading: true

---

## Implementing a custom provider

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from fastapi_tenancy import BaseIsolationProvider, TenancyConfig, Tenant
from sqlalchemy.ext.asyncio import AsyncSession

class MongoIsolationProvider(BaseIsolationProvider):
    """Route each tenant to a separate MongoDB collection namespace."""

    def __init__(self, config: TenancyConfig) -> None:
        super().__init__(config)
        # set up motor client here

    @asynccontextmanager
    async def get_session(self, tenant: Tenant) -> AsyncIterator[AsyncSession]:
        # For non-SQL backends this might yield a MongoDB collection instead.
        # The typing is relaxed — use Protocol if you need strict typing.
        yield self._get_collection(tenant)

    async def apply_filters(self, query, tenant: Tenant):
        return {**query, "tenant_id": tenant.id}

    async def initialize_tenant(self, tenant: Tenant) -> None:
        # Create collection, indexes, etc.
        pass

    async def destroy_tenant(self, tenant: Tenant) -> None:
        # Drop collection
        pass
```

Pass it to the manager:

```python
provider = MongoIsolationProvider(config)
app = FastAPI(
    lifespan=TenancyManager.create_lifespan(config, isolation_provider=provider)
)
```
