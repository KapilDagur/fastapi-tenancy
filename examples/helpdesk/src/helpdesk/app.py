r"""A multi-tenant helpdesk API.

What this demonstrates
----------------------
* **SCHEMA isolation** — every customer organisation gets its own PostgreSQL
  schema.  The ORM models carry no ``tenant_id`` column: the schema boundary
  *is* the isolation, so a handler cannot leak across tenants by forgetting a
  ``WHERE`` clause.
* **Header resolution** — the tenant comes from ``X-Tenant-ID``.  Swapping to
  subdomain or JWT resolution is a config change, not a code change.
* **Lifespan wiring** — the manager is created once and captured by the
  dependency factories, so no route ever reaches into ``app.state``.
* **Excluded paths** — ``/health`` and ``/admin`` bypass tenant resolution.
* **The in-process L1 cache without Redis** — ``l1_cache_enabled=True``.

Running it
----------
::

    docker run --rm -e POSTGRES_PASSWORD=pw -p 5432:5432 postgres:16-alpine
    export HELPDESK_DATABASE_URL="postgresql+asyncpg://postgres:pw@localhost/postgres"
    uv run uvicorn helpdesk.app:app --reload

Then::

    curl -XPOST localhost:8000/admin/tenants \
         -H 'content-type: application/json' \\
         -d '{"identifier":"acme-corp","name":"Acme","plan":"pro"}'

    curl -XPOST localhost:8000/tickets -H 'X-Tenant-ID: acme-corp' \
         -H 'content-type: application/json' \\
         -d '{"subject":"Printer on fire","requester_email":"ops@acme.test"}'

.. warning:: The ``/admin`` routes have no authentication

    They are deliberately unguarded to keep the example readable.  They
    provision and suspend tenants across the whole platform — put them behind
    an operator-only authn/authz layer, a separate port, or a private network
    before this shape goes anywhere near production.
"""

# NOTE: deliberately NO `from __future__ import annotations` in this module.
#
# FastAPI resolves route annotations at runtime with get_type_hints() when it
# builds the dependency graph.  With PEP 563 every annotation becomes a string
# evaluated against *module globals*, so a dependency held in a closure
# variable -- as `get_tenant_db` is here -- cannot be resolved.  FastAPI then
# silently downgrades `db` to a query parameter and every request fails with
# `{"loc": ["query", "db"], "msg": "Field required"}`.
#
# Either drop the future import (this file) or hoist dependencies to module
# scope.  Both work; keeping the app factory self-contained is worth more here.

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import os
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi_tenancy import (
    IsolationStrategy,
    ResolutionStrategy,
    SQLAlchemyTenantStore,
    TenancyConfig,
    TenancyManager,
    TenancyMiddleware,
)
from fastapi_tenancy.core.exceptions import TenancyError, TenantNotFoundError
from fastapi_tenancy.dependencies import (
    TenantDep,
    make_tenant_config_dependency,
    make_tenant_db_dependency,
)
from sqlalchemy import select

# Imported at RUNTIME, not under TYPE_CHECKING.  With
# `from __future__ import annotations` every annotation is a string, and
# FastAPI resolves them with get_type_hints() when it builds the dependency
# graph.  A name that exists only for the type checker cannot be resolved
# there, so FastAPI silently falls back to treating `db` as a *query
# parameter* and every request fails with
# `{"loc": ["query", "db"], "msg": "Field required"}`.
from sqlalchemy.ext.asyncio import AsyncSession

from helpdesk.models import Base, Ticket
from helpdesk.schemas import (
    TenantCreate,
    TenantOut,
    TenantQuota,
    TicketCreate,
    TicketOut,
    TicketUpdate,
)

DEFAULT_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"


def build_config(database_url: str | None = None) -> TenancyConfig:
    """Build the tenancy configuration for this example.

    Args:
        database_url: Async SQLAlchemy URL.  Defaults to ``HELPDESK_DATABASE_URL``
            from the environment, then to a local PostgreSQL.

    Returns:
        A configured :class:`~fastapi_tenancy.core.config.TenancyConfig`.
    """
    return TenancyConfig(
        database_url=database_url or os.getenv("HELPDESK_DATABASE_URL", DEFAULT_DATABASE_URL),
        # One schema per tenant. The ORM models stay unqualified and the
        # provider points search_path at the right schema per request.
        isolation_strategy=IsolationStrategy.SCHEMA,
        resolution_strategy=ResolutionStrategy.HEADER,
        tenant_header_name="X-Tenant-ID",
        # The in-process LRU needs no Redis. For multiple workers add
        # redis_url + cache_enabled=True so the cache is shared.
        l1_cache_enabled=True,
        l1_cache_ttl_seconds=30,
        enable_audit_logging=True,
        # Rate limiting requires Redis, so it is off here. To enable:
        #   redis_url="redis://localhost:6379/0",
        #   enable_rate_limiting=True,
        #   rate_limit_fail_closed=True,   # deny when Redis is unreachable
        enable_rate_limiting=False,
    )


def create_app(config: TenancyConfig | None = None) -> FastAPI:
    """Create the helpdesk application.

    Built as a factory rather than a module-level singleton so tests can inject
    a different database without touching the environment.

    Args:
        config: Tenancy configuration; defaults to :func:`build_config`.

    Returns:
        A wired :class:`fastapi.FastAPI` instance.
    """
    cfg = config or build_config()
    store = SQLAlchemyTenantStore(str(cfg.database_url))
    manager = TenancyManager(cfg, store)

    # Closure-captured at startup — no app.state lookups, so route handlers
    # have no startup-order dependency.
    get_tenant_db = make_tenant_db_dependency(manager)
    get_tenant_config = make_tenant_config_dependency(manager)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await manager.initialize()
        try:
            yield
        finally:
            await manager.close()

    app = FastAPI(
        title="Helpdesk API",
        summary="Multi-tenant support desk built on fastapi-tenancy.",
        lifespan=lifespan,
    )

    # Segment-anchored: "/admin" excludes /admin and /admin/..., but NOT
    # /administrator. Admin routes act across tenants, so they must not run
    # tenant resolution.
    app.add_middleware(
        TenancyMiddleware,
        manager=manager,
        excluded_paths=["/health", "/admin"],
    )

    app.state.manager = manager

    ####################
    # Operator surface #
    ####################

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        """Liveness probe. Excluded from tenancy, so it needs no header."""
        return {"status": "ok"}

    @app.post("/admin/tenants", status_code=status.HTTP_201_CREATED, tags=["admin"])
    async def provision_tenant(payload: TenantCreate) -> TenantOut:
        """Provision a customer organisation and its schema.

        ``register_tenant`` persists the row, then creates the schema and every
        table in ``Base.metadata`` inside it.  If provisioning fails it rolls
        the row back, so a failed onboarding does not poison the identifier.
        """
        quota = {"free": 5, "pro": 50, "enterprise": None}[payload.plan]
        try:
            tenant = await manager.register_tenant(
                identifier=payload.identifier,
                name=payload.name,
                metadata={"plan": payload.plan, "max_users": quota},
                app_metadata=Base.metadata,
            )
        except ValueError as exc:  # invalid slug
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        except TenancyError as exc:  # already exists, provisioning failure
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return TenantOut.model_validate(tenant, from_attributes=True)

    @app.get("/admin/tenants", tags=["admin"])
    async def list_tenants(limit: int = 50) -> list[TenantOut]:
        """List every tenant on the platform."""
        # manager.store, not the raw `store`: the manager wraps it in a
        # caching proxy, and going around the proxy bypasses cache handling.
        tenants = await manager.store.list(limit=limit)
        return [TenantOut.model_validate(t, from_attributes=True) for t in tenants]

    @app.post("/admin/tenants/{identifier}/suspend", tags=["admin"])
    async def suspend_tenant(identifier: str) -> TenantOut:
        """Suspend a tenant. Its requests get 403 from the next one onward.

        Goes through ``manager.suspend_tenant`` rather than
        ``store.set_status``.  The manager wraps the store in a caching proxy
        that invalidates the L1 entry on write; calling the raw store directly
        leaves the tenant cached as ACTIVE and suspension does not take effect
        until the entry expires -- up to ``l1_cache_ttl_seconds`` later.  For a
        suspension, that silent delay is a security problem, not a nuisance.
        """
        try:
            tenant = await manager.store.get_by_identifier(identifier)
        except TenantNotFoundError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such tenant") from exc
        updated = await manager.suspend_tenant(tenant.id)
        return TenantOut.model_validate(updated, from_attributes=True)

    ##################
    # Tenant surface #
    ##################

    @app.get("/me", tags=["tenant"])
    async def whoami(
        tenant: TenantDep,
        tenant_config: Annotated[Any, Depends(get_tenant_config)],
    ) -> TenantQuota:
        """Return the calling tenant and the quota parsed from its metadata."""
        return TenantQuota(
            tenant=TenantOut.model_validate(tenant, from_attributes=True),
            max_users=tenant_config.max_users,
            rate_limit_per_minute=tenant_config.rate_limit_per_minute,
            features_enabled=tenant_config.features_enabled,
        )

    @app.post("/tickets", status_code=status.HTTP_201_CREATED, tags=["tickets"])
    async def open_ticket(
        payload: TicketCreate,
        db: Annotated[AsyncSession, Depends(get_tenant_db)],
    ) -> TicketOut:
        """Open a ticket in the caller's schema.

        No tenant filter appears anywhere: the session's ``search_path`` already
        points at the caller's schema.
        """
        # No explicit begin(): the provider's session already has a
        # transaction open (it issued SET LOCAL search_path), and the context
        # manager wrapping get_session() closes it. Opening another raises
        # "A transaction is already begun on this Session."
        ticket = Ticket(
            subject=payload.subject,
            body=payload.body,
            requester_email=payload.requester_email,
        )
        db.add(ticket)
        await db.commit()
        await db.refresh(ticket)
        return TicketOut.model_validate(ticket, from_attributes=True)

    @app.get("/tickets", tags=["tickets"])
    async def list_tickets(
        db: Annotated[AsyncSession, Depends(get_tenant_db)],
        ticket_status: str | None = None,
    ) -> list[TicketOut]:
        """List the caller's tickets, newest first."""
        stmt = select(Ticket).order_by(Ticket.id.desc())
        if ticket_status is not None:
            stmt = stmt.where(Ticket.status == ticket_status)
        rows = (await db.execute(stmt)).scalars().all()
        return [TicketOut.model_validate(r, from_attributes=True) for r in rows]

    @app.get("/tickets/{ticket_id}", tags=["tickets"])
    async def get_ticket(
        ticket_id: int,
        db: Annotated[AsyncSession, Depends(get_tenant_db)],
    ) -> TicketOut:
        """Fetch one ticket by id.

        A ticket id belonging to a different tenant simply is not present in
        this schema, so it 404s — the isolation needs no explicit check.
        """
        row = await db.get(Ticket, ticket_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such ticket")
        return TicketOut.model_validate(row, from_attributes=True)

    @app.patch("/tickets/{ticket_id}", tags=["tickets"])
    async def update_ticket(
        ticket_id: int,
        payload: TicketUpdate,
        db: Annotated[AsyncSession, Depends(get_tenant_db)],
    ) -> TicketOut:
        """Apply a partial update to one of the caller's tickets."""
        changes = payload.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No fields to update")
        row = await db.get(Ticket, ticket_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such ticket")
        for field, value in changes.items():
            setattr(row, field, value)
        await db.commit()
        await db.refresh(row)
        return TicketOut.model_validate(row, from_attributes=True)

    @app.delete("/tickets/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["tickets"])
    async def delete_ticket(
        ticket_id: int,
        db: Annotated[AsyncSession, Depends(get_tenant_db)],
    ) -> None:
        """Delete one of the caller's tickets."""
        row = await db.get(Ticket, ticket_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such ticket")
        await db.delete(row)
        await db.commit()

    return app


app = create_app() if os.getenv("HELPDESK_DATABASE_URL") else None
"""Module-level app for ``uv run uvicorn helpdesk.app:app``.

``None`` unless ``HELPDESK_DATABASE_URL`` is set, so importing this module — as
the tests do, to call :func:`create_app` with their own database — never tries
to reach one.
"""

__all__ = ["DEFAULT_DATABASE_URL", "app", "build_config", "create_app"]
