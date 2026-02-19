# Basic Example — Notekeeper

A complete working app: header resolution + RLS isolation on SQLite (dev)
or PostgreSQL (production).

**Source:** [`examples/01-basic/`](https://github.com/your-org/fastapi-tenancy/tree/main/examples/01-basic)

---

## Project layout

```
01-basic/
├── app/
│   └── main.py       ← entire app in one file
├── tests/
│   └── test_notes.py
├── init.sql          ← PostgreSQL RLS policy (production)
└── requirements.txt
```

---

## `app/main.py`

```python
"""
Notekeeper — basic multi-tenant note-taking API.

Wiring pattern:
  * TenancyManager.create_lifespan() is the simplest integration —
    it registers TenancyMiddleware and handles init/shutdown.
  * For the manual pattern (more control) see the intermediate example.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import Column, String, Text, BigInteger, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from fastapi_tenancy import (
    TenancyConfig,
    TenancyManager,
    Tenant,
    TenantStatus,
    get_current_tenant,
)
from fastapi_tenancy.dependencies import get_tenant_db
from fastapi_tenancy.storage.memory import InMemoryTenantStore


# ── ORM ───────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class Note(Base):
    __tablename__ = "notes"
    id        = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(String(255), nullable=False, index=True)
    title     = Column(String(500), nullable=False)
    body      = Column(Text, nullable=False, default="")


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class NoteCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    body:  str = Field(default="", max_length=50_000)


class NoteRead(BaseModel):
    id:    int
    title: str
    body:  str
    model_config = {"from_attributes": True}


# ── Configuration ─────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "TENANCY_DATABASE_URL",
    "sqlite+aiosqlite:///./notekeeper_dev.db",
)

_tenant_store = InMemoryTenantStore()


async def _seed_tenants() -> None:
    for t in [
        Tenant(id="tenant-acme-001",   identifier="acme-corp", name="Acme Corporation"),
        Tenant(id="tenant-globex-001", identifier="globex",    name="Globex LLC"),
    ]:
        try:
            await _tenant_store.create(t)
        except ValueError:
            pass   # idempotent


config = TenancyConfig(
    database_url=DATABASE_URL,
    resolution_strategy="header",
    isolation_strategy="rls",
    tenant_header_name="X-Tenant-ID",
)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app):
    await _seed_tenants()

    # create_lifespan handles everything: middleware registration,
    # initialize(), and shutdown().  Pass the lifespan directly to FastAPI.
    # Here we build it inline so we can also create SQLite tables.
    from fastapi_tenancy.middleware.tenancy import TenancyMiddleware

    manager = TenancyManager(config, tenant_store=_tenant_store)
    app.add_middleware(TenancyMiddleware, config=config, manager=manager)
    app.state.tenancy_manager = manager
    await manager.initialize()
    app.state.tenant_store       = manager.tenant_store
    app.state.isolation_provider = manager.isolation_provider

    if "sqlite" in DATABASE_URL:
        engine = create_async_engine(DATABASE_URL)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    try:
        yield
    finally:
        await manager.shutdown()


app = FastAPI(
    title="Notekeeper",
    description="Basic multi-tenant note-taking API",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Routes ────────────────────────────────────────────────────────────────────

CurrentTenant = Annotated[Tenant, Depends(get_current_tenant)]
TenantSession = Annotated[AsyncSession, Depends(get_tenant_db)]


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/notes", response_model=list[NoteRead])
async def list_notes(tenant: CurrentTenant, session: TenantSession):
    """RLS ensures only this tenant's rows are visible — no WHERE needed."""
    result = await session.execute(select(Note))
    return result.scalars().all()


@app.post("/notes", response_model=NoteRead, status_code=status.HTTP_201_CREATED)
async def create_note(body: NoteCreate, tenant: CurrentTenant, session: TenantSession):
    note = Note(tenant_id=tenant.id, title=body.title, body=body.body)
    session.add(note)
    await session.commit()
    await session.refresh(note)
    return note


@app.get("/notes/{note_id}", response_model=NoteRead)
async def get_note(note_id: int, tenant: CurrentTenant, session: TenantSession):
    """404 when note doesn't exist *in this tenant's view* — avoids leaking IDs."""
    result = await session.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


@app.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(note_id: int, tenant: CurrentTenant, session: TenantSession):
    result = await session.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    await session.delete(note)
    await session.commit()


@app.get("/me")
async def me(tenant: CurrentTenant):
    return {
        "id":         tenant.id,
        "identifier": tenant.identifier,
        "name":       tenant.name,
        "status":     tenant.status,
    }
```

---

## Run it

```bash
cd examples/01-basic
pip install -r requirements.txt
uvicorn app.main:app --reload

# Resolve tenant from header
curl -H "X-Tenant-ID: acme-corp" http://localhost:8000/me
# {"id":"tenant-acme-001","identifier":"acme-corp","name":"Acme Corporation","status":"active"}

# Create a note
curl -s -X POST http://localhost:8000/notes \
  -H "X-Tenant-ID: acme-corp" \
  -H "Content-Type: application/json" \
  -d '{"title":"Hello","body":"World"}'

# Missing header → 400
curl http://localhost:8000/me

# Unknown tenant → 404
curl -H "X-Tenant-ID: nobody" http://localhost:8000/me

# Health check — no tenant header required
curl http://localhost:8000/health
```

---

## Key design points

### `TenancyManager.create_lifespan()` — simplest wiring

```python
# The one-liner pattern: zero boilerplate
app = FastAPI(
    lifespan=TenancyManager.create_lifespan(
        config,
        tenant_store=my_store,
        skip_paths=["/health", "/docs"],
    )
)
```

`create_lifespan` is a classmethod that returns a proper lifespan callable.
It registers `TenancyMiddleware` **before** yield (the only Starlette-safe
registration point) and handles `initialize()` / `shutdown()`.

### Tenant isolation via RLS

RLS isolation sets `app.current_tenant` on every session.
PostgreSQL policies then filter rows automatically:

```sql
-- init.sql (run once at DB setup)
ALTER TABLE notes ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON notes
    USING (tenant_id = current_setting('app.current_tenant'));
```

For SQLite (dev) the library injects `WHERE tenant_id = :id` automatically.

### Tenant provisioning

```python
import uuid
from fastapi import Request
from fastapi_tenancy import Tenant, TenantStatus, TenancyManager

@app.post("/admin/tenants", status_code=201)
async def create_tenant(request: Request, body: TenantCreate):
    manager: TenancyManager = request.app.state.tenancy_manager

    tenant = Tenant(
        id=str(uuid.uuid4()),
        identifier=body.slug,
        name=body.name,
        status=TenantStatus.ACTIVE,
        metadata={"plan": body.plan},
    )
    created = await manager.tenant_store.create(tenant)
    # Pass your SQLAlchemy metadata so tables are created in the tenant's schema
    await manager.isolation_provider.initialize_tenant(created, metadata=Base.metadata)
    return {"id": created.id, "identifier": created.identifier}
```

---

## Tests

```bash
pytest tests/ -v --cov=app
```

The test suite patches the lifespan to use an in-memory store and SQLite —
no Docker required. See `tests/test_notes.py` for the full pattern.

!!! tip "Middleware registration in tests"
    When patching `app.router.lifespan_context` in tests, always call
    `app.add_middleware(TenancyMiddleware, ...)` inside the patched lifespan
    **before** the `yield`. This is what the production code does too.
