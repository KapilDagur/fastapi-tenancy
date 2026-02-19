"""
Projectr — Advanced multi-tenant SaaS example.

Architecture
------------
* JWT resolution      — Bearer token carries tenant_id claim
* Hybrid isolation    — Enterprise tenants → schema, Starter → RLS
* Redis caching       — Per-tenant key namespace via TenantCache
* Audit logging       — Every write logged via AuditLog model
* Health + metrics    — /health, /metrics endpoints
* Background workers  — Project creation triggers async notifications

Key wiring pattern
------------------
TenancyManager takes NO ``app`` argument and has no ``setup_middleware``
method.  Register TenancyMiddleware manually inside the lifespan
(before yield) — the only Starlette-safe registration point.

AuditLog is imported from fastapi_tenancy.core.types (it IS exported
from the top-level fastapi_tenancy namespace too).
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from fastapi_tenancy import (
    AuditLog,           # exported from fastapi_tenancy namespace
    TenancyConfig,
    TenancyManager,
    Tenant,
    TenantStatus,
    get_current_tenant,
)
from fastapi_tenancy.cache.tenant_cache import TenantCache
from fastapi_tenancy.dependencies import get_tenant_db
from fastapi_tenancy.middleware.tenancy import TenancyMiddleware
from fastapi_tenancy.storage.memory import InMemoryTenantStore

from app.models import Base, Project, Task
from app.services import ProjectService

log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

DATABASE_URL     = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./projectr_dev.db")
REDIS_URL        = os.getenv("REDIS_URL")
JWT_SECRET       = os.getenv("JWT_SECRET", "dev-secret-key-change-in-production-32ch")
JWT_ALGORITHM    = os.getenv("JWT_ALGORITHM", "HS256")
JWT_TENANT_CLAIM = os.getenv("JWT_TENANT_CLAIM", "tenant_id")
# premium_tenants is a list of tenant IDs (not identifiers) that get schema isolation
PREMIUM_TENANTS  = os.getenv("PREMIUM_TENANTS", "tenant-acme-001,tenant-techcorp-001").split(",")


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class TokenRequest(BaseModel):
    tenant_id: str
    secret: str = "demo"   # toy auth — use a real IdP in production


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    tenant_id: str


class ProjectCreate(BaseModel):
    name:        str = Field(..., min_length=1, max_length=500)
    description: str = Field(default="", max_length=10_000)


class ProjectRead(BaseModel):
    id:          int
    name:        str
    description: str
    status:      str
    created_at:  datetime
    model_config = {"from_attributes": True}


class TaskCreate(BaseModel):
    title:    str = Field(..., min_length=1, max_length=500)
    assignee: str | None = None


class TaskRead(BaseModel):
    id:         int
    project_id: int
    title:      str
    status:     str
    assignee:   str | None
    created_at: datetime
    model_config = {"from_attributes": True}


class TaskStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(todo|in_progress|done|blocked)$")


# ── Global optional cache ─────────────────────────────────────────────────────

_tenant_cache: TenantCache | None = None


def get_cache() -> TenantCache | None:
    return _tenant_cache


# ── JWT helpers ───────────────────────────────────────────────────────────────

def create_access_token(tenant_id: str) -> str:
    try:
        from jose import jwt as _jwt
    except ImportError:
        raise RuntimeError("python-jose is required: pip install python-jose[cryptography]")
    payload = {
        JWT_TENANT_CLAIM: tenant_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
        "iat": datetime.now(timezone.utc),
    }
    return _jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


# ── App config factory ────────────────────────────────────────────────────────

def make_config(db_url: str = DATABASE_URL) -> TenancyConfig:
    return TenancyConfig(
        database_url=db_url,
        resolution_strategy="jwt",
        isolation_strategy="hybrid",
        jwt_secret=JWT_SECRET,
        jwt_algorithm=JWT_ALGORITHM,
        jwt_tenant_claim=JWT_TENANT_CLAIM,
        premium_tenants=PREMIUM_TENANTS,
        premium_isolation_strategy="schema",
        standard_isolation_strategy="rls",
        enable_audit_logging=True,
        redis_url=REDIS_URL,
        cache_enabled=REDIS_URL is not None,
    )


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tenant_cache

    config = make_config()

    # Seed tenants for dev/test (in-memory store)
    store = InMemoryTenantStore()
    for t in [
        Tenant(
            id="tenant-acme-001",
            identifier="acme-corp",
            name="Acme Corporation",
            metadata={"plan": "enterprise", "max_projects": 500},
        ),
        Tenant(
            id="tenant-techcorp-001",
            identifier="tech-corp",
            name="TechCorp Inc",
            metadata={"plan": "enterprise", "max_projects": 500},
        ),
        Tenant(
            id="tenant-startup-001",
            identifier="startup-x",
            name="Startup X",
            metadata={"plan": "starter", "max_projects": 10},
        ),
        Tenant(
            id="tenant-dev-001",
            identifier="dev-labs",
            name="Dev Labs",
            metadata={"plan": "starter", "max_projects": 10},
        ),
    ]:
        try:
            await store.create(t)
        except ValueError:
            pass

    manager = TenancyManager(config, tenant_store=store)

    # ── Middleware — MUST be before yield ─────────────────────────────────────
    app.add_middleware(
        TenancyMiddleware,
        config=config,
        manager=manager,
        skip_paths=["/health", "/metrics", "/docs", "/redoc", "/openapi.json", "/auth"],
    )
    app.state.tenancy_manager = manager
    app.state.tenancy_config  = config

    await manager.initialize()

    app.state.tenant_store       = manager.tenant_store
    app.state.isolation_provider = manager.isolation_provider

    # Create tables for SQLite dev mode
    if "sqlite" in DATABASE_URL:
        engine = create_async_engine(DATABASE_URL)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    # Initialise isolation for each active tenant.
    # Pass metadata= so tables are created inside each tenant's schema.
    tenants = await store.list(status=TenantStatus.ACTIVE)
    for tenant in tenants:
        try:
            await manager.isolation_provider.initialize_tenant(tenant, metadata=Base.metadata)
        except Exception as exc:
            log.warning("Init tenant %s: %s", tenant.identifier, exc)

    # Optional Redis cache
    if REDIS_URL:
        _tenant_cache = TenantCache(redis_url=REDIS_URL, default_ttl=300)

    try:
        yield
    finally:
        if _tenant_cache:
            await _tenant_cache.close()
            _tenant_cache = None
        await manager.shutdown()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Projectr",
    description="Advanced multi-tenant project management API",
    version="3.0.0",
    lifespan=lifespan,
)

# ── Dependencies ──────────────────────────────────────────────────────────────

CurrentTenant = Annotated[Tenant, Depends(get_current_tenant)]
TenantSession = Annotated[AsyncSession, Depends(get_tenant_db)]
Cache         = Annotated[TenantCache | None, Depends(get_cache)]


def get_service(
    session: TenantSession,
    tenant: CurrentTenant,
    cache: Cache,
) -> ProjectService:
    return ProjectService(session=session, tenant=tenant, cache=cache)


Service = Annotated[ProjectService, Depends(get_service)]


# ── Auth (toy — production uses a real identity provider) ─────────────────────

@app.post("/auth/token", response_model=TokenResponse, tags=["auth"])
async def get_token(body: TokenRequest):
    """
    Issue a JWT for the given tenant_id.
    Production: validate credentials against your IdP before issuing.
    """
    if not body.secret:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(body.tenant_id)
    return TokenResponse(access_token=token, token_type="bearer", tenant_id=body.tenant_id)


# ── Health + metrics ──────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/metrics", tags=["ops"])
async def metrics(tenant: CurrentTenant, session: TenantSession):
    """Per-tenant usage metrics."""
    from sqlalchemy import func
    from fastapi_tenancy import TenantConfig

    project_count = (await session.execute(
        select(func.count()).select_from(Project).where(Project.tenant_id == tenant.id)
    )).scalar_one()
    task_count = (await session.execute(
        select(func.count()).select_from(Task).where(Task.tenant_id == tenant.id)
    )).scalar_one()

    # Build TenantConfig directly from metadata — get_tenant_config is a
    # FastAPI dependency (Depends) so it cannot be called directly here.
    cfg = TenantConfig(
        rate_limit_per_minute=tenant.metadata.get("rate_limit_per_minute", 100),
        max_users=tenant.metadata.get("max_users"),
    )
    return {
        "tenant_id":    tenant.id,
        "plan":         tenant.metadata.get("plan", "unknown"),
        "projects":     project_count,
        "tasks":        task_count,
        "max_projects": tenant.metadata.get("max_projects"),
        "rate_limit":   cfg.rate_limit_per_minute,
    }


@app.get("/me", tags=["tenant"])
async def me(tenant: CurrentTenant):
    return {
        "id":         tenant.id,
        "identifier": tenant.identifier,
        "name":       tenant.name,
        "plan":       tenant.metadata.get("plan", "starter"),
        "tier":       "enterprise" if tenant.id in PREMIUM_TENANTS else "starter",
    }


# ── Projects ──────────────────────────────────────────────────────────────────

@app.get("/projects", response_model=list[ProjectRead], tags=["projects"])
async def list_projects(svc: Service, status_filter: str | None = None):
    """List projects. Results are cached in Redis with a 5-minute TTL."""
    return await svc.list_projects(status=status_filter)


@app.post("/projects", response_model=ProjectRead, status_code=201, tags=["projects"])
async def create_project(body: ProjectCreate, svc: Service, tenant: CurrentTenant):
    """
    Create a project.
    Starter tenants have a plan-level project cap; enterprise is unlimited.
    """
    plan_limit = tenant.metadata.get("max_projects")
    if plan_limit is not None:
        existing = await svc.list_projects()
        if len(existing) >= int(plan_limit):
            raise HTTPException(
                status_code=429,
                detail=f"Project limit of {plan_limit} reached. Please upgrade your plan.",
            )
    return await svc.create_project(name=body.name, description=body.description)


@app.get("/projects/{project_id}", response_model=ProjectRead, tags=["projects"])
async def get_project(project_id: int, svc: Service):
    project = await svc.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.patch("/projects/{project_id}", response_model=ProjectRead, tags=["projects"])
async def update_project(project_id: int, body: ProjectCreate, svc: Service):
    project = await svc.update_project(
        project_id, name=body.name, description=body.description
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@app.delete("/projects/{project_id}", status_code=204, tags=["projects"])
async def delete_project(project_id: int, svc: Service):
    deleted = await svc.delete_project(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")


# ── Tasks ─────────────────────────────────────────────────────────────────────

@app.get("/projects/{project_id}/tasks", response_model=list[TaskRead], tags=["tasks"])
async def list_tasks(project_id: int, svc: Service):
    return await svc.list_tasks(project_id)


@app.post(
    "/projects/{project_id}/tasks",
    response_model=TaskRead,
    status_code=201,
    tags=["tasks"],
)
async def create_task(project_id: int, body: TaskCreate, svc: Service):
    task = await svc.create_task(
        project_id=project_id, title=body.title, assignee=body.assignee
    )
    if not task:
        raise HTTPException(status_code=404, detail="Project not found")
    return task


@app.patch("/tasks/{task_id}/status", response_model=TaskRead, tags=["tasks"])
async def update_task_status(task_id: int, body: TaskStatusUpdate, svc: Service):
    task = await svc.update_task_status(task_id, body.status)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task
