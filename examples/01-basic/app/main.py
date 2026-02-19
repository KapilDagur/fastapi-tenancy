"""
Notekeeper — Basic multi-tenant SaaS example.

Architecture
------------
* Header resolution  — X-Tenant-ID: acme-corp
* RLS isolation      — SET app.current_tenant before every query
* PostgreSQL backend — single shared database, all tenants in one schema
* In-memory store    — replaced by PostgreSQL store in production
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import Column, String, Text, BigInteger, select, text
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

# ── SQLAlchemy ORM ────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class Note(Base):
    __tablename__ = "notes"

    id        = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(String(255), nullable=False, index=True)
    title     = Column(String(500), nullable=False)
    body      = Column(Text, nullable=False, default="")


# ── Schemas ───────────────────────────────────────────────────────────────────

class NoteCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    body: str = Field(default="", max_length=50_000)


class NoteRead(BaseModel):
    id: int
    title: str
    body: str

    model_config = {"from_attributes": True}


# ── App setup ─────────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "TENANCY_DATABASE_URL",
    "sqlite+aiosqlite:///./notekeeper_dev.db",
)

# Pre-seed tenants for the in-memory store (production uses PostgreSQLTenantStore)
_tenant_store = InMemoryTenantStore()


async def _seed_tenants() -> None:
    """Seed two demo tenants into the in-memory store."""
    for t in [
        Tenant(id="tenant-acme-001",   identifier="acme-corp", name="Acme Corporation"),
        Tenant(id="tenant-globex-001", identifier="globex",    name="Globex LLC"),
    ]:
        try:
            await _tenant_store.create(t)
        except ValueError:
            pass  # already exists (idempotent)


config = TenancyConfig(
    database_url=DATABASE_URL,
    resolution_strategy="header",
    isolation_strategy="rls",
    tenant_header_name="X-Tenant-ID",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bootstrap: seed tenants → initialise manager → run → shutdown."""
    await _seed_tenants()

    manager = TenancyManager(config, tenant_store=_tenant_store)

    # Middleware MUST be registered before yield — only Starlette-safe point.
    from fastapi_tenancy.middleware.tenancy import TenancyMiddleware
    app.add_middleware(TenancyMiddleware, config=config, manager=manager)
    app.state.tenancy_manager = manager

    await manager.initialize()
    app.state.tenant_store       = manager.tenant_store
    app.state.isolation_provider = manager.isolation_provider

    # Ensure tables exist (SQLite dev mode; Postgres uses init.sql)
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
    """Public health-check endpoint — no tenant required."""
    return {"status": "ok"}


@app.get("/notes", response_model=list[NoteRead])
async def list_notes(
    tenant: CurrentTenant,
    session: TenantSession,
):
    """
    List all notes for the current tenant.

    RLS ensures rows from other tenants are invisible — no WHERE clause needed.
    """
    result = await session.execute(select(Note))
    return result.scalars().all()


@app.post("/notes", response_model=NoteRead, status_code=status.HTTP_201_CREATED)
async def create_note(
    body: NoteCreate,
    tenant: CurrentTenant,
    session: TenantSession,
):
    """Create a note scoped to the current tenant."""
    note = Note(tenant_id=tenant.id, title=body.title, body=body.body)
    session.add(note)
    await session.commit()
    await session.refresh(note)
    return note


@app.get("/notes/{note_id}", response_model=NoteRead)
async def get_note(
    note_id: int,
    tenant: CurrentTenant,
    session: TenantSession,
):
    """
    Fetch a single note by ID.

    RLS prevents cross-tenant access automatically — a 404 is returned
    when the note doesn't exist *in this tenant's view*, not a 403.
    This avoids leaking whether the note ID exists at all.
    """
    result = await session.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


@app.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    note_id: int,
    tenant: CurrentTenant,
    session: TenantSession,
):
    """Delete a note. RLS prevents deleting another tenant's notes."""
    result = await session.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    await session.delete(note)
    await session.commit()


@app.get("/me")
async def me(tenant: CurrentTenant):
    """Return the resolved tenant object — useful for debugging."""
    return {
        "id": tenant.id,
        "identifier": tenant.identifier,
        "name": tenant.name,
        "status": tenant.status,
    }
