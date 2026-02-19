# Advanced Example — Hybrid Isolation + Background Workers

A full enterprise SaaS pattern with:

- **JWT resolution** — tenant embedded in Bearer token
- **Hybrid isolation** — schema for premium tenants, RLS for standard
- **Redis caching** — `TenantCache` with per-tenant key namespacing
- **Custom resolver** — API key → tenant lookup
- **Alembic migrations** in CI/CD pipeline
- **Celery workers** with tenant context propagation

**Source:** [`examples/03-advanced/`](https://github.com/your-org/fastapi-tenancy/tree/main/examples/03-advanced)

---

## Architecture

```
                         ┌─────────────┐
Client ──Bearer JWT────▶  │  FastAPI App │
                         │             │
                         │  Hybrid     │
                         │  Provider   │
                         │  /       \  │
                         │ schema   rls│
                         └──────┬──────┘
                                │ tasks
                                ▼
                         ┌─────────────┐
                         │  Celery     │
                         │  Worker     │
                         │  (tenant    │
                         │   context   │
                         │  via Redis) │
                         └─────────────┘
```

---

## Configuration

```python
import os
from fastapi_tenancy import TenancyConfig

config = TenancyConfig(
    database_url=os.environ["DATABASE_URL"],
    resolution_strategy="jwt",
    isolation_strategy="hybrid",

    # Premium tenants get their own dedicated schema
    premium_isolation_strategy="schema",
    # Standard tenants share tables, separated by RLS
    standard_isolation_strategy="rls",
    # List of premium tenant IDs (not identifiers)
    premium_tenants=os.environ["PREMIUM_TENANT_IDS"].split(","),

    jwt_secret=os.environ["JWT_SECRET"],
    jwt_algorithm="HS256",
    jwt_tenant_claim="tenant_id",

    database_pool_size=25,
    database_max_overflow=15,
    database_pool_recycle=1800,

    cache_enabled=True,
    redis_url=os.environ["REDIS_URL"],
)
```

---

## App wiring

```python
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi_tenancy import TenancyManager, SQLAlchemyTenantStore, TenantStatus
from fastapi_tenancy.middleware.tenancy import TenancyMiddleware
from fastapi_tenancy.storage.redis import RedisTenantStore
from myapp.models import Base

primary = SQLAlchemyTenantStore(database_url=str(config.database_url))
store   = RedisTenantStore(redis_url=os.environ["REDIS_URL"], primary_store=primary, ttl=120)


@asynccontextmanager
async def lifespan(app: FastAPI):
    manager = TenancyManager(config, tenant_store=store)

    # Middleware MUST be registered before yield
    app.add_middleware(
        TenancyMiddleware,
        config=config,
        manager=manager,
        skip_paths=["/health", "/metrics", "/docs"],
    )
    app.state.tenancy_manager = manager

    await manager.initialize()
    app.state.tenant_store       = manager.tenant_store
    app.state.isolation_provider = manager.isolation_provider

    # Initialise isolation for every active tenant.
    # metadata= is required so CREATE SCHEMA + tables run.
    tenants = await store.list(status=TenantStatus.ACTIVE)
    for tenant in tenants:
        await manager.isolation_provider.initialize_tenant(tenant, metadata=Base.metadata)

    try:
        yield
    finally:
        await manager.shutdown()

app = FastAPI(lifespan=lifespan)
```

---

## Custom resolver — API key

```python
from fastapi import Request
from fastapi_tenancy import (
    BaseTenantResolver,   # exported from top-level package
    Tenant,
    TenantResolutionError,
)
from fastapi_tenancy.storage.tenant_store import TenantStore


class ApiKeyResolver(BaseTenantResolver):
    """Resolve tenant from X-API-Key header via a database lookup."""

    def __init__(self, tenant_store: TenantStore) -> None:
        super().__init__(tenant_store)

    async def resolve(self, request: Request) -> Tenant:
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            raise TenantResolutionError(
                reason="X-API-Key header is required",
                strategy="api-key",
            )
        # search() does in-memory substring match by default;
        # subclass TenantStore to use a DB-level index for production
        results = await self.tenant_store.search(api_key, limit=1)
        if not results:
            raise TenantResolutionError(
                reason="Invalid API key",
                strategy="api-key",
            )
        return list(results)[0]


# Wire it up by passing resolver= to TenancyManager
manager = TenancyManager(config, tenant_store=store, resolver=ApiKeyResolver(store))
```

---

## Dynamic premium tier (database-driven)

Instead of a static list, override `is_premium_tenant` with a fast
in-memory set that is refreshed from the database at startup:

```python
from fastapi_tenancy.core.config import TenancyConfig as _Base


class AppConfig(_Base):
    """TenancyConfig subclass with dynamic premium tier resolution."""

    _premium_set: set[str] = set()

    def is_premium_tenant(self, tenant_id: str) -> bool:
        # Called synchronously from HybridIsolationProvider._get_provider.
        # Must be fast — use an in-memory set, not a database call.
        return tenant_id in self._premium_set

    def refresh_premium_set(self, tenant_ids: set[str]) -> None:
        self._premium_set = tenant_ids


# During lifespan startup:
async with manager.tenant_scope("some-admin-id"):
    premium_ids = {t.id async for t in store.list() if t.metadata.get("plan") == "enterprise"}
    config.refresh_premium_set(premium_ids)
```

---

## Celery worker with tenant context

Celery tasks run in separate processes — you must propagate the tenant ID
and re-establish context in the worker:

```python
# tasks.py
import asyncio, os
from celery import Celery
from fastapi_tenancy import TenancyConfig
from fastapi_tenancy.core.context import TenantContext
from fastapi_tenancy.storage.postgres import SQLAlchemyTenantStore
from fastapi_tenancy.isolation.factory import IsolationProviderFactory

celery_app = Celery("myapp", broker=os.environ["REDIS_URL"])
config     = TenancyConfig()


@celery_app.task(name="tasks.process_report")
def process_report(tenant_id: str, report_id: str) -> None:
    """Run in a worker process — must bootstrap tenant context from scratch."""
    asyncio.run(_async_process_report(tenant_id, report_id))


async def _async_process_report(tenant_id: str, report_id: str) -> None:
    store    = SQLAlchemyTenantStore(database_url=str(config.database_url))
    tenant   = await store.get_by_id(tenant_id)
    provider = IsolationProviderFactory.create(config.isolation_strategy, config)

    async with TenantContext.scope(tenant):
        async with provider.get_session(tenant) as session:
            report = await session.get(Report, report_id)
            result = await generate_report(report)
            await save_result(session, report_id, result)
```

---

## Testing hybrid isolation

```python
import pytest
from fastapi_tenancy import TenancyConfig, Tenant
from fastapi_tenancy.isolation.hybrid import HybridIsolationProvider
from fastapi_tenancy.isolation.schema import SchemaIsolationProvider
from fastapi_tenancy.isolation.rls import RLSIsolationProvider


@pytest.fixture
def hybrid_config() -> TenancyConfig:
    return TenancyConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        resolution_strategy="header",
        isolation_strategy="hybrid",
        premium_isolation_strategy="schema",
        standard_isolation_strategy="rls",
        premium_tenants=["premium-tenant-001"],
    )


def test_premium_tenant_gets_schema_provider(hybrid_config):
    provider = HybridIsolationProvider(hybrid_config)

    premium  = Tenant(id="premium-tenant-001", identifier="big-co",   name="Big Co")
    standard = Tenant(id="standard-001",       identifier="small-co", name="Small Co")

    assert isinstance(provider._get_provider(premium),  SchemaIsolationProvider)
    assert isinstance(provider._get_provider(standard), RLSIsolationProvider)
```

---

## CI/CD migration pipeline

```yaml
# .github/workflows/deploy.yml
jobs:
  migrate:
    name: Run tenant migrations
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install -e ".[migrations,postgres]"
      - name: Migrate all tenants
        run: python scripts/migrate_all.py
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}

  deploy:
    needs: migrate   # only deploy after migrations succeed
    runs-on: ubuntu-latest
    steps:
      - run: kubectl set image deployment/app app=${{ env.IMAGE_TAG }}
```

```python
# scripts/migrate_all.py
import asyncio, sys
from fastapi_tenancy import TenancyConfig
from fastapi_tenancy.storage.postgres import SQLAlchemyTenantStore
from fastapi_tenancy.isolation.factory import IsolationProviderFactory
from fastapi_tenancy.migrations.manager import MigrationManager


async def main():
    config   = TenancyConfig()
    store    = SQLAlchemyTenantStore(database_url=str(config.database_url))
    await store.initialize()

    provider = IsolationProviderFactory.create(config.isolation_strategy, config)
    migrator = MigrationManager("alembic.ini", provider)

    tenants = await store.list(limit=50_000)
    print(f"Migrating {len(tenants)} tenants…")

    results = await migrator.upgrade_all_tenants(tenants)
    print(f"Done — success={results['success']}, failed={results['failed']}")

    if results["failed"]:
        sys.exit(1)


asyncio.run(main())
```

---

## RLS tenant data cleanup

For RLS (standard tier) tenants, `destroy_tenant` must know which tables
to purge. Pass your SQLAlchemy metadata — the library auto-detects tables
with a `tenant_id` column:

```python
from myapp.models import Base

# Deletes all rows with tenant_id = tenant.id from every table
# that has a tenant_id column
await manager.isolation_provider.destroy_tenant(
    tenant,
    metadata=Base.metadata,
)
```

---

## Tests

```bash
cd examples/03-advanced
pip install -r requirements.txt
pytest tests/unit/ -v              # no external services
pytest tests/integration/ -v       # SQLite in-memory
pytest tests/e2e/ -v --postgres    # requires Docker Compose
```
