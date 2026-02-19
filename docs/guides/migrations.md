# Migrations

`MigrationManager` integrates Alembic with multi-tenant setups, routing
migrations to the correct schema or database based on your isolation strategy.

## Install

```bash
pip install fastapi-tenancy[migrations]
# or
pip install alembic
```

## How it works per strategy

| Strategy | What happens |
|----------|-------------|
| **Schema** | Sets Alembic's `version_locations` and `search_path` per tenant |
| **Database** | Sets `sqlalchemy.url` to the tenant-specific database URL |
| **RLS** | Migrates the shared schema once — all tenants share the result |
| **Hybrid** | Delegates to the sub-provider chosen for that tenant's tier |

All Alembic calls are wrapped in `asyncio.run_in_executor` to prevent
blocking the event loop — Alembic's command API is synchronous.

## Setup

### 1. Initialise Alembic

```bash
alembic init alembic
```

### 2. Configure `alembic.ini`

```ini
[alembic]
script_location = alembic
sqlalchemy.url = %(TENANCY_DATABASE_URL)s
```

### 3. Update `alembic/env.py`

```python
from fastapi_tenancy import TenancyConfig
from myapp.models import Base  # your declarative base

config = TenancyConfig()

def run_migrations_online():
    connectable = create_async_engine(str(config.database_url))
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
        )
        with context.begin_transaction():
            context.run_migrations()
```

## Migrate all tenants

```python
from fastapi_tenancy import MigrationManager
from fastapi_tenancy.isolation.schema import SchemaIsolationProvider

provider = SchemaIsolationProvider(config)
manager = MigrationManager(
    alembic_ini_path="alembic.ini",
    isolation_provider=provider,
)

# Fetch all tenants and migrate
tenants = await tenant_store.list(limit=1000)
results = await manager.upgrade_all_tenants(tenants)
print(f"Success: {results['success']}, Failed: {results['failed']}")
```

## Migrate one tenant

```python
await manager.upgrade_tenant(tenant, revision="head")
```

## Check migration status

```python
status = await manager.get_migration_status(tenant)
print(status)
# {
#   "current_revision": "a1b2c3d4",
#   "head_revision": "a1b2c3d4",
#   "is_up_to_date": True
# }
```

## Run on startup (optional)

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.add_middleware(TenancyMiddleware, config=config, manager=manager)
    await manager.initialize()

    # Auto-migrate all tenants at startup
    migration_manager = MigrationManager("alembic.ini", manager.isolation_provider)
    tenants = await manager.tenant_store.list(limit=10_000)
    results = await migration_manager.upgrade_all_tenants(tenants)
    logger.info("Migrations: %s", results)

    yield
    await manager.shutdown()
```

!!! warning "Startup migrations in production"
    Running migrations at startup works but adds startup latency proportional
    to the number of tenants.  For large deployments, run migrations as a
    separate pre-deployment job (e.g., a Kubernetes init container or a CI/CD
    pipeline step) rather than at application startup.

## CI/CD pre-deployment migration job

```yaml
# .github/workflows/deploy.yml
- name: Run tenant migrations
  run: |
    python scripts/migrate_all_tenants.py --revision head
  env:
    TENANCY_DATABASE_URL: ${{ secrets.DATABASE_URL }}
```

```python
# scripts/migrate_all_tenants.py
import asyncio
from fastapi_tenancy import TenancyConfig
from fastapi_tenancy.storage.postgres import SQLAlchemyTenantStore
from fastapi_tenancy.isolation.schema import SchemaIsolationProvider
from fastapi_tenancy.migrations.manager import MigrationManager

async def main():
    config = TenancyConfig()
    store = SQLAlchemyTenantStore(database_url=str(config.database_url))
    await store.initialize()

    provider = SchemaIsolationProvider(config)
    migration_manager = MigrationManager("alembic.ini", provider)

    tenants = await store.list(limit=10_000)
    results = await migration_manager.upgrade_all_tenants(tenants)
    print(f"Migrated {results['success']}/{results['total']} tenants")
    if results["failed"] > 0:
        exit(1)

asyncio.run(main())
```
