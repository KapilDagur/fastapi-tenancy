"""
Invoicer — Intermediate multi-tenant SaaS example.

Architecture
------------
* Subdomain resolution — acme.localhost:8000, globex.localhost:8000
* Schema isolation     — each tenant gets a dedicated PostgreSQL schema
                         (tenant_acme_corp, tenant_globex …)
* Alembic migrations   — per-tenant schema upgrade/downgrade
* Redis cache          — tenant metadata caching layer
* SQLAlchemy store     — persistent tenant registry (PostgreSQL in prod)

Key wiring pattern
------------------
Do NOT use TenancyManager(app=app) or manager.setup_middleware().
Instead register TenancyMiddleware manually inside the lifespan
function (before yield) which is the only safe registration point.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi_tenancy import (
    SQLAlchemyTenantStore,  # canonical name — PostgreSQLTenantStore is deprecated
    TenancyConfig,
    TenancyManager,
    Tenant,
    TenantStatus,
    get_current_tenant,
)
from fastapi_tenancy.middleware.tenancy import TenancyMiddleware
from fastapi_tenancy.storage.memory import InMemoryTenantStore

from app.api import router

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
    """
    Startup sequence:
    1. Build config + tenant store.
    2. Register TenancyMiddleware BEFORE yield (required by Starlette).
    3. Call manager.initialize() — connects storage, builds resolver.
    4. For each active tenant call initialize_tenant(metadata=...) so
       CREATE SCHEMA + table DDL runs on first boot.
    5. Yield — app serves requests.
    6. Shutdown cleanly.
    """
    config = make_config()

    # Use in-memory store for SQLite (dev/test); SQLAlchemy store otherwise.
    # SQLAlchemyTenantStore is the canonical name; PostgreSQLTenantStore is
    # a deprecated alias kept for backwards compatibility only.
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

    # ── Middleware registration ────────────────────────────────────────────────
    # MUST happen before yield — Starlette rebuilds the middleware stack on
    # first request; add_middleware after startup raises RuntimeError.
    app.add_middleware(
        TenancyMiddleware,
        config=config,
        manager=manager,
        skip_paths=["/health", "/metrics", "/docs", "/redoc", "/openapi.json"],
    )

    # Expose handles to app state so dependencies can reach them
    app.state.tenancy_manager = manager
    app.state.tenancy_config  = config

    await manager.initialize()

    app.state.tenant_store       = manager.tenant_store
    app.state.isolation_provider = manager.isolation_provider

    # Initialise each tenant's schema + tables on startup
    from app.models import Base
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


app = FastAPI(
    title="Invoicer",
    description="Intermediate multi-tenant invoicing API with schema isolation",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(router, tags=["invoicing"])


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/me")
async def me(tenant: Annotated[Tenant, Depends(get_current_tenant)]):
    schema = tenant.schema_name or f"tenant_{tenant.identifier.replace('-', '_')}"
    return {
        "id":         tenant.id,
        "identifier": tenant.identifier,
        "name":       tenant.name,
        "status":     tenant.status,
        "schema":     schema,
    }
