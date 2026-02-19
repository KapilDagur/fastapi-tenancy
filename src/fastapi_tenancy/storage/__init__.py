"""Storage implementations for tenant data.

This module provides multiple storage backends for tenant metadata:
- PostgreSQL: Production persistent storage
- Redis: High-performance caching layer
- In-Memory: Testing and development

Example:
    ```python
    # Production: PostgreSQL with Redis cache
    from fastapi_tenancy.storage.postgres import PostgreSQLTenantStore
    from fastapi_tenancy.storage.redis import RedisTenantStore

    primary = PostgreSQLTenantStore(
        database_url="postgresql+asyncpg://localhost/tenancy"
    )
    await primary.initialize()

    cache = RedisTenantStore(
        redis_url="redis://localhost:6379/0",
        primary_store=primary,
        ttl=3600,
    )

    # Use cache layer (automatically uses primary on miss)
    tenant = await cache.get_by_id("123")

    # Development: In-Memory
    from fastapi_tenancy.storage.memory import InMemoryTenantStore

    store = InMemoryTenantStore()
    tenant = await store.create(Tenant(...))
    ```
"""

from fastapi_tenancy.storage.memory import InMemoryTenantStore
from fastapi_tenancy.storage.postgres import PostgreSQLTenantStore, TenantModel
from fastapi_tenancy.storage.tenant_store import TenantStore

# Optional — requires: pip install fastapi-tenancy[redis]
try:
    from fastapi_tenancy.storage.redis import RedisTenantStore
except ImportError:
    RedisTenantStore = None  # type: ignore[assignment, misc]

__all__ = [
    "InMemoryTenantStore",
    "PostgreSQLTenantStore",
    "RedisTenantStore",
    "TenantModel",
    "TenantStore",
]
