# Intermediate Example — Invoicing SaaS

A realistic invoicing SaaS with:

- **Subdomain resolution** — `acme.example.com`, `globex.example.com`
- **Schema isolation** — one PostgreSQL schema per tenant
- **Redis cache** — sub-millisecond tenant lookups
- **Tenant provisioning** — admin creates new tenants with Alembic migration
- **Background task** — PDF generation in tenant context

**Source:** [`examples/02-intermediate/`](https://github.com/your-org/fastapi-tenancy/tree/main/examples/02-intermediate)

---

## Project layout

```
02-intermediate/
├── app/
│   ├── main.py
│   ├── api/__init__.py     ← invoice + customer routes
│   ├── models/__init__.py
│   └── schemas/__init__.py
├── alembic/
│   └── env.py
├── tests/
│   └── test_invoicer.py
└── requirements.txt
```

---

## `app/main.py`

```python
"""
Invoicer — schema-isolation SaaS with subdomain resolution.

Wiring pattern
--------------
  - TenancyManager takes NO ``app`` argument.
  - Middleware is registered via app.add_middleware() inside the lifespan
    BEFORE yield — the only Starlette-safe registration point.
  - Use SQLAlchemyTenantStore (canonical name).
    PostgreSQLTenantStore is a deprecated alias kept for backwards compat.
  - Always pass metadata= to initialize_tenant() so tables are created.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi_tenancy import (
    SQLAlchemyTenantStore,   # canonical — PostgreSQLTenantStore is deprecated
    TenancyConfig,
    TenancyManager,
    Tenant,
    TenantStatus,
    get_current_tenant,
)
from fastapi_tenancy.middleware.tenancy import TenancyMiddleware
from fastapi_tenancy.storage.memory import InMemoryTenantStore

from app.api import router
from app.models import Base

log = logging.getLogger(__name__)

DATABASE_URL  = os.getenv("TENANCY_DATABASE_URL", "sqlite+aiosqlite:///./invoicer_dev.db")
DOMAIN_SUFFIX = os.getenv("TENANCY_DOMAIN_SUFFIX", ".localhost")
REDIS_URL     = os.getenv("TENANCY_REDIS_URL")


def make_config(db_url: str = DATABASE_URL) -> TenancyConfig:
    return TenancyConfig(
        database_url=db_url,
        resolution_strategy="subdomain",
        isolation_strategy="schema",
        domain_suffix=DOMAIN_SUFFIX,
        schema_prefix="tenant_",
        redis_url=REDIS_URL,
        cache_enabled=REDIS_URL is not None,
        enable_audit_logging=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = make_config()

    # SQLite → in-memory store (dev/test); Postgres → SQLAlchemyTenantStore
    if "sqlite" in DATABASE_URL:
        store = InMemoryTenantStore()
        for t in [
            Tenant(id="tenant-acme-001",   identifier="acme-corp", name="Acme Corp"),
            Tenant(id="tenant-globex-001", identifier="globex",    name="Globex LLC"),
        ]:
            try:
                await store.create(t)
            except ValueError:
                pass
    else:
        store = SQLAlchemyTenantStore(database_url=DATABASE_URL, pool_size=5)
        await store.initialize()

    manager = TenancyManager(config, tenant_store=store)

    # Middleware MUST be added before yield
    app.add_middleware(
        TenancyMiddleware,
        config=config,
        manager=manager,
        skip_paths=["/health", "/metrics", "/docs", "/redoc", "/openapi.json"],
    )
    app.state.tenancy_manager = manager

    await manager.initialize()
    app.state.tenant_store       = manager.tenant_store
    app.state.isolation_provider = manager.isolation_provider

    # Create each tenant's schema + tables.
    # metadata= is required — without it tables are not created.
    tenants = await store.list(status=TenantStatus.ACTIVE)
    for tenant in tenants:
        try:
            await manager.isolation_provider.initialize_tenant(tenant, metadata=Base.metadata)
        except Exception as exc:
            log.warning("Could not init tenant %s: %s", tenant.identifier, exc)

    try:
        yield
    finally:
        await manager.shutdown()


app = FastAPI(title="Invoicer", version="2.0.0", lifespan=lifespan)
app.include_router(router, tags=["invoicing"])

@app.get("/health")
async def health():
    return {"status": "ok"}
```

---

## `app/models/__init__.py`

```python
from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class InvoiceStatus(str, Enum):
    DRAFT     = "draft"
    SENT      = "sent"
    PAID      = "paid"
    OVERDUE   = "overdue"
    CANCELLED = "cancelled"


class Customer(Base):
    __tablename__ = "customers"
    id         = Column(BigInteger, primary_key=True, autoincrement=True)
    name       = Column(String(255), nullable=False)
    email      = Column(String(255), nullable=False, unique=True)
    company    = Column(String(255))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    invoices   = relationship("Invoice", back_populates="customer", lazy="select")


class Invoice(Base):
    __tablename__ = "invoices"
    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    customer_id = Column(BigInteger, ForeignKey("customers.id"), nullable=False)
    number      = Column(String(50),  nullable=False, unique=True)
    status      = Column(String(20),  nullable=False, default=InvoiceStatus.DRAFT)
    amount      = Column(Numeric(12, 2), nullable=False)
    currency    = Column(String(3),   nullable=False, default="USD")
    due_date    = Column(DateTime(timezone=True))
    notes       = Column(Text, default="")
    created_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    customer    = relationship("Customer", back_populates="invoices")


class LineItem(Base):
    __tablename__ = "line_items"
    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    invoice_id  = Column(BigInteger, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    description = Column(String(500), nullable=False)
    quantity    = Column(Numeric(10, 2), nullable=False, default=1)
    unit_price  = Column(Numeric(12, 2), nullable=False)
```

---

## Admin: tenant provisioning with Alembic migration

```python
# app/api/admin.py
import uuid
from fastapi import APIRouter, Request
from pydantic import BaseModel
from fastapi_tenancy import TenancyManager, Tenant, TenantStatus
from fastapi_tenancy.migrations.manager import MigrationManager
from app.models import Base

router = APIRouter()


class TenantProvision(BaseModel):
    slug: str
    name: str
    plan: str = "starter"


@router.post("/admin/tenants", status_code=201)
async def provision_tenant(body: TenantProvision, request: Request):
    """Create a new tenant: store record → schema → run Alembic migrations."""
    manager: TenancyManager = request.app.state.tenancy_manager

    tenant = Tenant(
        id=str(uuid.uuid4()),
        identifier=body.slug,
        name=body.name,
        status=TenantStatus.ACTIVE,
        metadata={"plan": body.plan},
    )

    # 1. Persist to tenant store
    created = await manager.tenant_store.create(tenant)

    # 2. Create schema + tables (metadata= is required)
    await manager.isolation_provider.initialize_tenant(created, metadata=Base.metadata)

    # 3. Run Alembic migrations in tenant's schema
    migrator = MigrationManager("alembic.ini", manager.isolation_provider)
    await migrator.upgrade_tenant(created, revision="head")

    return created


@router.delete("/admin/tenants/{tenant_id}")
async def deprovision_tenant(tenant_id: str, request: Request):
    """GDPR delete — wipe all tenant data and remove schema."""
    manager: TenancyManager = request.app.state.tenancy_manager
    tenant = await manager.tenant_store.get_by_id(tenant_id)

    # DROP SCHEMA ... CASCADE — removes all tenant tables at once
    await manager.isolation_provider.destroy_tenant(tenant)
    await manager.tenant_store.delete(tenant_id)

    return {"status": "deleted", "tenant_id": tenant_id}
```

---

## Background task with tenant context

```python
# tasks.py — background PDF generation in tenant context
from fastapi_tenancy import TenancyManager

async def generate_pdf(invoice_id: str, tenant_id: str) -> None:
    """
    Background tasks run in a fresh asyncio task — TenantContext is NOT
    copied automatically from the request that enqueued us.
    Use manager.tenant_scope() to set context for the task's duration.
    """
    from myapp.main import app
    manager: TenancyManager = app.state.tenancy_manager

    # tenant_scope sets TenantContext and yields the Tenant object
    async with manager.tenant_scope(tenant_id) as tenant:
        async with manager.isolation_provider.get_session(tenant) as session:
            invoice = await session.get(Invoice, invoice_id)
            pdf_bytes = render_pdf(invoice)
            await upload_to_storage(
                f"{tenant.identifier}/{invoice.number}.pdf", pdf_bytes
            )
```

---

## Redis write-through cache

```python
from fastapi_tenancy import SQLAlchemyTenantStore
from fastapi_tenancy.storage.redis import RedisTenantStore

# Primary store — persists to PostgreSQL
primary = SQLAlchemyTenantStore(database_url=DATABASE_URL, pool_size=10)

# Redis cache in front — warm reads from Redis, writes through to primary
cached_store = RedisTenantStore(
    redis_url=REDIS_URL,
    primary_store=primary,
    ttl=300,   # 5-minute TTL
)

# Pass cached_store to TenancyManager
manager = TenancyManager(config, tenant_store=cached_store)
```

---

## Tests

```bash
cd examples/02-intermediate
pip install -r requirements.txt
pytest tests/ -v
```

Tests use SQLite + header resolution (no subdomain DNS tricks required).
The fixture patches `app.router.lifespan_context` to inject the in-memory
store and correct config — no env-var monkey-patching needed.

!!! note "SQLAlchemyTenantStore vs PostgreSQLTenantStore"
    `PostgreSQLTenantStore` is a deprecated alias.  Always use
    `SQLAlchemyTenantStore` in new code — it is the actual implementation
    and works with any SQLAlchemy-supported database.
