# fastapi-tenancy

**Enterprise-grade multi-tenancy for FastAPI.** Schema, database, RLS, and hybrid
isolation strategies — all async, SQL-injection-safe, Pydantic v2, and production-ready.

[![CI](https://github.com/KapilDagur/fastapi-tenancy/actions/workflows/ci.yml/badge.svg)](https://github.com/KapilDagur/fastapi-tenancy/actions)
[![Coverage](https://codecov.io/gh/KapilDagur/fastapi-tenancy/branch/main/graph/badge.svg)](https://codecov.io/gh/KapilDagur/fastapi-tenancy)
[![PyPI](https://img.shields.io/pypi/v/fastapi-tenancy.svg)](https://pypi.org/project/fastapi-tenancy/)
[![Python](https://img.shields.io/pypi/pyversions/fastapi-tenancy.svg)](https://pypi.org/project/fastapi-tenancy/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/KapilDagur/fastapi-tenancy/blob/main/LICENSE)
[![Typed](https://img.shields.io/badge/typed-py.typed-blue)](https://peps.python.org/pep-0561/)

---

## What is fastapi-tenancy?

`fastapi-tenancy` eliminates the multi-tenancy boilerplate in FastAPI SaaS apps.
Instead of scattering `WHERE tenant_id = ?` clauses throughout your codebase and
manually managing schemas, configure once — the library handles everything else.

```python
from fastapi import FastAPI
from fastapi_tenancy import TenancyManager, TenancyConfig

config = TenancyConfig(
    database_url="postgresql+asyncpg://user:pass@localhost/myapp",
    resolution_strategy="header",   # X-Tenant-ID header
    isolation_strategy="schema",    # one PostgreSQL schema per tenant
)

# One line — manager + middleware + lifespan wired correctly
app = FastAPI(lifespan=TenancyManager.create_lifespan(config))
```

---

## Architecture

```mermaid
graph LR
    subgraph Request Pipeline
        R[Request] --> M[TenancyMiddleware]
        M --> RS[Resolver\nheader / subdomain / jwt / path]
        RS --> ST[TenantStore\nPostgreSQL / Memory / Redis]
        ST --> M
        M --> C[TenantContext\ncontextvars — async-safe]
        C --> H[Route Handler]
    end

    subgraph Handler Pipeline
        H --> D[FastAPI Dependencies\nget_tenant_db\nget_current_tenant]
        D --> I[IsolationProvider\nschema / rls / database / hybrid]
        I --> DB[(Database)]
    end
```

**Key design principle**: every component in the request pipeline is
**swappable via Protocol/ABC** — you can replace any resolver, store, or
isolation provider with a custom implementation, and the type checker will
verify correctness.

---

## Feature Matrix

| Feature | Detail |
|---|---|
| **Isolation strategies** | Schema, Database, RLS, Hybrid |
| **Resolution strategies** | Header, Subdomain, JWT, URL Path, Custom |
| **Databases** | PostgreSQL (full), SQLite (dev/CI), MySQL, MSSQL |
| **Async-first** | `AsyncSession`, `create_async_engine`, `contextvars` |
| **SQL-injection-safe** | Every DDL identifier validated + double-quoted |
| **Type-safe** | `py.typed` shipped, full mypy support, Protocols + ABCs |
| **Pydantic v2** | Frozen `Tenant` model, `model_dump_json`, `field_validator` |
| **FastAPI lifespan** | Middleware registered correctly before `yield` |
| **Optional extras** | Redis cache, Alembic migrations, JWT parsing |
| **Tested** | 75 %+ coverage, pytest unit/integration/e2e matrix |

---

## 60-Second Quickstart

```bash
pip install "fastapi-tenancy[postgres]"
```

```python
from fastapi import FastAPI, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_tenancy import (
    TenancyConfig, TenancyManager,
    Tenant, get_current_tenant, get_tenant_db,
)

config = TenancyConfig(
    database_url="postgresql+asyncpg://user:pass@localhost/myapp",
    resolution_strategy="header",
    isolation_strategy="schema",
)

app = FastAPI(lifespan=TenancyManager.create_lifespan(config))


@app.get("/me")
async def who_am_i(tenant: Tenant = Depends(get_current_tenant)):
    return {"tenant": tenant.identifier}


@app.get("/users")
async def list_users(
    session: AsyncSession = Depends(get_tenant_db),
):
    # session.search_path already set to tenant's schema — no WHERE needed
    result = await session.execute(select(User))
    return result.scalars().all()
```

```bash
# Request with tenant header
curl -H "X-Tenant-ID: acme-corp" http://localhost:8000/me
# {"tenant": "acme-corp"}

# Request without header → 400 Bad Request
curl http://localhost:8000/me
# {"error": "tenant_resolution_failed", "message": "..."}
```

---

## Installation Extras

```bash
pip install "fastapi-tenancy[postgres]"    # PostgreSQL + asyncpg
pip install "fastapi-tenancy[sqlite]"      # SQLite + aiosqlite
pip install "fastapi-tenancy[mysql]"       # MySQL + aiomysql
pip install "fastapi-tenancy[redis]"       # Redis cache layer
pip install "fastapi-tenancy[jwt]"         # JWT tenant resolution
pip install "fastapi-tenancy[migrations]"  # Alembic migrations
pip install "fastapi-tenancy[full]"        # Everything
pip install "fastapi-tenancy[dev]"         # Dev + test + lint
```

---

## Next Steps

- **[Installation](getting-started/installation.md)** — detailed setup instructions
- **[Quickstart](getting-started/quickstart.md)** — step-by-step complete app
- **[Isolation Strategies](guides/isolation.md)** — choose schema, database, RLS, or hybrid
- **[Production Deployment](guides/production.md)** — pool sizing, security, observability
- **[API Reference](api/index.md)** — complete auto-generated API docs
