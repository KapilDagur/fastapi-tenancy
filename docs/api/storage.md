# Storage

The storage layer handles **CRUD operations on tenants**.  It is deliberately
separate from the isolation layer, which handles **data separation per
tenant**.

## Class hierarchy

```
TenantStore (ABC)
├── InMemoryTenantStore    # testing
├── PostgreSQLTenantStore  # deprecated alias → SQLAlchemyTenantStore
├── SQLAlchemyTenantStore  # production (PostgreSQL, MySQL, SQLite)
└── RedisTenantStore       # write-through Redis cache on top of any store
```

## TenantStore ABC

::: fastapi_tenancy.storage.tenant_store.TenantStore
    options:
      show_source: true
      show_root_heading: true
      members_order: source
      filters: ["!^_"]

---

## SQLAlchemyTenantStore

The production storage backend.  Works with all async SQLAlchemy dialects:
PostgreSQL, MySQL, SQLite.

!!! tip "Preferred name"
    `SQLAlchemyTenantStore` is the preferred name.  `PostgreSQLTenantStore`
    is a deprecated alias that emits a `DeprecationWarning` — update your
    imports.

::: fastapi_tenancy.storage.postgres.SQLAlchemyTenantStore
    options:
      show_source: false
      show_root_heading: true
      members_order: source
      filters: ["!^_"]

---

## InMemoryTenantStore

Thread-safe, async in-memory store for unit tests.  No database required.

::: fastapi_tenancy.storage.memory.InMemoryTenantStore
    options:
      show_source: false
      show_root_heading: true
      members_order: source

---

## RedisTenantStore

Write-through cache.  Reads are served from Redis; writes always go to the
primary store first then refresh the cache.

::: fastapi_tenancy.storage.redis.RedisTenantStore
    options:
      show_source: false
      show_root_heading: true
      members_order: source

---

## Implementing a custom store

Subclass `TenantStore` and implement the eight abstract methods:

```python
from fastapi_tenancy import TenantStore, Tenant, TenantStatus
from typing import Sequence

class DynamoDBTenantStore(TenantStore):
    def __init__(self, table_name: str) -> None:
        self.table_name = table_name

    async def get_by_id(self, tenant_id: str) -> Tenant:
        item = await dynamo.get_item(Key={"pk": tenant_id})
        if not item:
            raise TenantNotFoundError(tenant_id=tenant_id)
        return Tenant(**item["Item"])

    async def get_by_identifier(self, identifier: str) -> Tenant:
        ...

    async def create(self, tenant: Tenant) -> Tenant:
        ...

    async def update(self, tenant: Tenant) -> Tenant:
        ...

    async def delete(self, tenant_id: str) -> None:
        ...

    async def list(self, *, limit: int = 50, offset: int = 0,
                   status: TenantStatus | None = None) -> Sequence[Tenant]:
        ...

    async def search(self, query: str, *, limit: int = 50) -> Sequence[Tenant]:
        ...

    async def count(self, *, status: TenantStatus | None = None) -> int:
        ...
```

Then pass it to the manager:

```python
store = DynamoDBTenantStore(table_name="tenants")
app = FastAPI(
    lifespan=TenancyManager.create_lifespan(config, tenant_store=store)
)
```

## `search()` performance note

The default `search()` in `InMemoryTenantStore` loads tenants in memory and
filters in Python — fine for tests, problematic for thousands of tenants.
Production stores should override `search()` with a native database query:

```python
# PostgreSQL full-text search example
async def search(self, query: str, *, limit: int = 50) -> Sequence[Tenant]:
    async with AsyncSession(self._engine) as session:
        result = await session.execute(
            select(TenantModel)
            .where(
                or_(
                    TenantModel.identifier.ilike(f"%{query}%"),
                    TenantModel.name.ilike(f"%{query}%"),
                )
            )
            .limit(limit)
        )
        rows = result.scalars().all()
    return [Tenant(**row.to_dict()) for row in rows]
```
