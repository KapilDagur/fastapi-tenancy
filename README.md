# fastapi-tenancy

**Enterprise-grade multi-tenancy for FastAPI.** Schema, database, RLS, and hybrid isolation strategies — all async, production-ready, and backed by a 95%+ test suite.

[![CI](https://github.com/yourusername/fastapi-tenancy/actions/workflows/ci.yml/badge.svg)](https://github.com/yourusername/fastapi-tenancy/actions)
[![Coverage](https://codecov.io/gh/yourusername/fastapi-tenancy/branch/main/graph/badge.svg)](https://codecov.io/gh/yourusername/fastapi-tenancy)
[![PyPI](https://img.shields.io/pypi/v/fastapi-tenancy.svg)](https://pypi.org/project/fastapi-tenancy/)
[![Python](https://img.shields.io/pypi/pyversions/fastapi-tenancy.svg)](https://pypi.org/project/fastapi-tenancy/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## Features

- **Four isolation strategies** — schema-per-tenant, database-per-tenant, Row-Level Security, and hybrid (mix strategies by plan tier)
- **Multi-database** — PostgreSQL (full), SQLite (dev/CI), MySQL, MSSQL with automatic dialect detection and graceful fallback
- **Async-first** — built on `AsyncSession`, `create_async_engine`, and `contextvars` — zero blocking I/O
- **SQL-injection-safe** — every DDL identifier is validated and quoted before execution
- **FastAPI lifespan** — clean `initialize()` / `shutdown()` lifecycle hooks
- **Flexible resolution** — header, subdomain, path, JWT, or custom resolver
- **Optional services** — Redis cache, Alembic migrations, JWT parsing — all optional deps
- **Pydantic v2** — full `model_dump_json`, `model_validate_json`, `frozen=True` models throughout

---

## Quickstart

```bash
pip install fastapi-tenancy[postgres]
```

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi_tenancy import (
    TenancyConfig, TenancyManager,
    TenantContext, get_current_tenant, Tenant,
)

config = TenancyConfig(
    database_url="postgresql+asyncpg://user:pass@localhost/myapp",
    resolution_strategy="header",   # X-Tenant-ID header
    isolation_strategy="schema",    # one schema per tenant
)

manager = TenancyManager(config)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await manager.initialize()
    yield
    await manager.shutdown()

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    manager.middleware_class,
    config=config,
    manager=manager,
)

@app.get("/me")
async def who_am_i(tenant: Tenant = Depends(get_current_tenant)):
    return {"tenant": tenant.identifier, "plan": tenant.metadata.get("plan")}
```

---

## Installation

| Extra | Installs |
|-------|----------|
| `[postgres]` | `asyncpg` driver |
| `[sqlite]` | `aiosqlite` driver (dev / CI) |
| `[mysql]` | `aiomysql` driver |
| `[mssql]` | `aioodbc` driver |
| `[redis]` | `redis[hiredis]` |
| `[jwt]` | `python-jose[cryptography]` |
| `[migrations]` | `alembic` |
| `[full]` | postgres + redis + jwt + migrations |
| `[dev]` | everything + test + lint tools |

```bash
# Production (PostgreSQL + Redis + Alembic)
pip install "fastapi-tenancy[full]"

# Development / CI (SQLite, no external services needed)
pip install "fastapi-tenancy[sqlite,dev]"
```

---

## Isolation Strategies

### Schema isolation (PostgreSQL / MSSQL)

Each tenant gets a dedicated schema. Tables are identical across schemas; the `search_path` is set per-request.

```python
config = TenancyConfig(
    database_url="postgresql+asyncpg://...",
    isolation_strategy="schema",
    schema_prefix="tenant_",          # e.g. tenant_acme_corp
)
```

**SQLite / unknown dialects** automatically fall back to a table-name prefix (`t_<slug>_<table>`), so your dev environment and CI need no PostgreSQL.

### Database isolation

Each tenant owns a separate database/file. Maximum isolation; higher operational overhead.

```python
config = TenancyConfig(
    database_url="postgresql+asyncpg://user:pass@host/master",
    isolation_strategy="database",
    # Optional URL template for per-tenant connections:
    # database_url_template="postgresql+asyncpg://user:pass@host/{database_name}"
)
```

### Row-Level Security

All tenants share one schema; PostgreSQL RLS policies enforce data boundaries. Lowest resource overhead.

```python
config = TenancyConfig(
    database_url="postgresql+asyncpg://...",
    isolation_strategy="rls",
)
```

Non-PostgreSQL dialects fall back to explicit `WHERE tenant_id = :id` filters applied via `apply_filters()`.

### Hybrid isolation

Premium tenants get schema isolation; standard tenants share via RLS.

```python
config = TenancyConfig(
    database_url="postgresql+asyncpg://...",
    isolation_strategy="hybrid",
    premium_isolation_strategy="schema",
    standard_isolation_strategy="rls",
)
```

---

## Tenant Resolution

```python
# Header (default)
config = TenancyConfig(resolution_strategy="header",    header_name="X-Tenant-ID")

# Subdomain: acme.example.com → "acme"
config = TenancyConfig(resolution_strategy="subdomain", domain_suffix="example.com")

# URL path: /tenants/acme/orders → "acme"
config = TenancyConfig(resolution_strategy="path",      path_prefix="/tenants/")

# JWT claim
config = TenancyConfig(resolution_strategy="jwt",       jwt_tenant_claim="tenant_id")
```

---

## Tenant Store

```python
from fastapi_tenancy import TenancyManager
from fastapi_tenancy.storage.postgres import PostgreSQLTenantStore
from fastapi_tenancy.core.types import Tenant, TenantStatus

store = PostgreSQLTenantStore(database_url="postgresql+asyncpg://...")
await store.initialize()

# Create
tenant = await store.create(Tenant(
    id="acme-001",
    identifier="acme-corp",
    name="Acme Corp",
    metadata={"plan": "enterprise", "region": "us-east-1"},
))

# Query
by_id   = await store.get_by_id("acme-001")
by_slug = await store.get_by_identifier("acme-corp")
active  = await store.list(status=TenantStatus.ACTIVE, skip=0, limit=20)

# Update
await store.set_status("acme-001", TenantStatus.SUSPENDED)
await store.update_metadata("acme-001", {"plan": "starter"})
```

> **SQLite dev setup** — use `InMemoryTenantStore` or `PostgreSQLTenantStore("sqlite+aiosqlite:///:memory:")` with no external service.

---

## Accessing the Tenant in Route Handlers

```python
from fastapi import Depends
from fastapi_tenancy import get_current_tenant, get_current_tenant_optional, Tenant

@app.get("/dashboard")
async def dashboard(tenant: Tenant = Depends(get_current_tenant)):
    return {"id": tenant.id, "plan": tenant.metadata.get("plan")}

@app.get("/public")
async def public(tenant: Tenant | None = Depends(get_current_tenant_optional)):
    return {"tenant": tenant.identifier if tenant else "anonymous"}
```

---

## Migrations (Alembic)

```python
from fastapi_tenancy.migrations.manager import TenantMigrationManager

migrator = TenantMigrationManager(config, alembic_config_path="alembic.ini")

# Upgrade all active tenants
result = await migrator.upgrade_all_tenants(continue_on_error=True)
print(f"{result['success']} succeeded, {result['failed']} failed")

# Per-tenant
await migrator.upgrade(tenant, revision="head")
status = await migrator.get_migration_status(tenant)
```

---

## Security

- **DDL injection** — `assert_safe_schema_name()` / `assert_safe_database_name()` validate every identifier before DDL. Identifiers are also double-quoted.
- **Parameterised SQL** — session variables are always set via bind parameters, never string formatting.
- **Timing-safe comparisons** — `constant_time_compare()` uses `hmac.compare_digest`.
- **Context isolation** — `TenantContext` uses `contextvars.ContextVar`; concurrent async tasks are fully isolated with no shared state.

---

## Testing

```bash
# Full suite (needs PostgreSQL + Redis for some tests)
pytest

# CI-friendly — SQLite only, no external services
pytest -m "not e2e"

# With coverage report
pytest --cov --cov-report=term-missing
```

The test suite ships with SQLite-backed integration tests that require **no external services**, achieving ≥ 80% coverage by default.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to set up a dev environment, run tests, and submit a pull request.

---

## License

[MIT](LICENSE)
