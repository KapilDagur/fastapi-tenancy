"""Central tenancy manager — lifecycle, middleware, and component orchestration.

Design decisions
----------------
* ``TenancyManager.__init__`` accepts **no** ``app`` argument.  The FastAPI
  application is only needed for two operations: registering middleware and
  storing app-level state.  Both happen inside ``create_lifespan`` where the
  real ``app`` instance is already available.  Removing ``app`` from
  ``__init__`` makes the manager independently constructable and testable.

* **Middleware registration timing** — FastAPI (Starlette) raises a
  ``RuntimeError`` if ``add_middleware`` is called after the application has
  started (i.e. after the lifespan has begun).  ``TenancyMiddleware`` is
  therefore registered *before* the lifespan's ``yield`` by calling
  ``app.add_middleware`` inside the lifespan callable, which runs during the
  ASGI startup phase — before the first request arrives but while middleware
  stacking is still allowed.

  The correct pattern::

      @asynccontextmanager
      async def lifespan(app):
          app.add_middleware(TenancyMiddleware, ...)  # ← BEFORE yield
          await manager.initialize()
          yield                                       # ← app now serving
          await manager.shutdown()

* **create_lifespan** is the recommended, one-call integration::

      app = FastAPI(lifespan=TenancyManager.create_lifespan(config))

Changes from v0.1.0
-------------------
- ``app`` parameter removed from ``__init__`` (was only used for middleware
  registration and app.state — both now happen inside ``create_lifespan``).
- ``setup_middleware()`` is no longer a method on the manager.  Middleware is
  wired automatically inside ``create_lifespan``.  Advanced users who need
  manual wiring can call ``app.add_middleware(TenancyMiddleware, ...)``
  *before* their lifespan yields.
- ``lifespan()`` renamed to ``create_lifespan()`` (static factory method).
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi_tenancy.core.context import TenantContext
from fastapi_tenancy.core.types import (
    Tenant,
    TenantStatus,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI
    from starlette.types import Lifespan

    from fastapi_tenancy.core.config import TenancyConfig
    from fastapi_tenancy.isolation.base import BaseIsolationProvider
    from fastapi_tenancy.resolution.base import BaseTenantResolver
    from fastapi_tenancy.storage.tenant_store import TenantStore

logger = logging.getLogger(__name__)


class TenancyManager:
    """Orchestrator for all multi-tenancy components.

    The manager is **app-agnostic**: it holds configuration and references to
    storage, resolver, and isolation components without coupling to a specific
    FastAPI application instance.  This makes it easy to test in isolation and
    to share across multiple apps if needed.

    Lifecycle
    ---------
    1. **Construct**: validates config, stores component references — no I/O.
    2. **initialize()**: creates storage tables, isolation namespaces, default
       tenants, etc.  All heavy I/O happens here.
    3. **shutdown()**: disposes engines, closes Redis connections, etc.

    The recommended integration uses :meth:`create_lifespan`::

        app = FastAPI(lifespan=TenancyManager.create_lifespan(config))

    Advanced manual wiring::

        manager = TenancyManager(config)

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            # Middleware MUST be registered before the lifespan yield.
            # FastAPI/Starlette raises RuntimeError if add_middleware is
            # called after startup has completed.
            from fastapi_tenancy.middleware.tenancy import TenancyMiddleware
            app.add_middleware(TenancyMiddleware, manager=manager)

            await manager.initialize()
            yield
            await manager.shutdown()

        app = FastAPI(lifespan=lifespan)

    Parameters
    ----------
    config:
        Validated :class:`~fastapi_tenancy.core.config.TenancyConfig`.
    tenant_store:
        Override the default :class:`~fastapi_tenancy.storage.postgres.SQLAlchemyTenantStore`.
        Useful for testing with :class:`~fastapi_tenancy.storage.memory.InMemoryTenantStore`.
    resolver:
        Override the resolver created from ``config.resolution_strategy``.
    isolation_provider:
        Override the isolation provider created from ``config.isolation_strategy``.
    """

    def __init__(
        self,
        config: TenancyConfig,
        *,
        tenant_store: TenantStore | None = None,
        resolver: BaseTenantResolver | None = None,
        isolation_provider: BaseIsolationProvider | None = None,
    ) -> None:
        self.config = config
        self._initialized = False

        # Validate config before touching any I/O
        self._validate_config()

        # Store overrides — actual objects are created lazily in initialize()
        self._custom_store = tenant_store
        self._custom_resolver = resolver
        self._custom_isolation = isolation_provider

        # These are set during initialize()
        self.tenant_store: TenantStore
        self.resolver: BaseTenantResolver
        self.isolation_provider: BaseIsolationProvider

        logger.info(
            "TenancyManager created resolution=%s isolation=%s",
            config.resolution_strategy.value,
            config.isolation_strategy.value,
        )

    def _validate_config(self) -> None:
        # Cross-field validation is handled by TenancyConfig.validate_configuration()
        # which runs automatically at config construction time via model_post_init.
        # No additional checks needed here.
        pass

    async def initialize(self) -> None:
        """Initialise all components (storage, resolver, isolation, defaults).

        Safe to call multiple times — subsequent calls are no-ops.
        This method performs all I/O: creating database engines, running
        schema creation, connecting to Redis, etc.
        """
        if self._initialized:
            return

        logger.info("TenancyManager initialising …")

        self._initialize_storage()
        self._initialize_resolver()
        self._initialize_isolation()

        if hasattr(self.tenant_store, "initialize"):
            await self.tenant_store.initialize()

        if hasattr(self.isolation_provider, "initialize"):
            await self.isolation_provider.initialize()

        await self._create_default_tenants()

        self._initialized = True
        logger.info("TenancyManager initialised")

    async def shutdown(self) -> None:
        """Release all resources (connection pools, Redis connections …)."""
        if not self._initialized:
            return

        logger.info("TenancyManager shutting down …")

        if hasattr(self.tenant_store, "close"):
            await self.tenant_store.close()

        if hasattr(self.isolation_provider, "close"):
            await self.isolation_provider.close()

        self._initialized = False
        logger.info("TenancyManager shutdown complete")

    async def __aenter__(self) -> TenancyManager:
        """Support ``async with TenancyManager(config) as m:`` in tests."""
        await self.initialize()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.shutdown()

    @staticmethod
    def create_lifespan(
        config: TenancyConfig,
        *,
        tenant_store: TenantStore | None = None,
        resolver: BaseTenantResolver | None = None,
        isolation_provider: BaseIsolationProvider | None = None,
        skip_paths: list[str] | None = None,
        debug_headers: bool = False,
    ) -> Lifespan[FastAPI]:
        """Build a FastAPI ``lifespan`` context manager that manages a
        :class:`TenancyManager`.

        This is the **recommended** integration pattern::

            from fastapi import FastAPI
            from fastapi_tenancy import TenancyManager, TenancyConfig

            config = TenancyConfig(
                database_url="postgresql+asyncpg://...",
                resolution_strategy="header",
                isolation_strategy="schema",
            )

            app = FastAPI(lifespan=TenancyManager.create_lifespan(config))

        The lifespan callable:

        1. Creates the :class:`TenancyManager`.
        2. Registers :class:`~fastapi_tenancy.middleware.tenancy.TenancyMiddleware`
           on the *real* app **before** yielding — the only point at which
           FastAPI/Starlette allows middleware registration.
        3. Calls ``manager.initialize()`` to perform all I/O setup.
        4. Yields (application serves requests).
        5. Calls ``manager.shutdown()`` on teardown.

        Parameters
        ----------
        config:
            Validated tenancy configuration.
        tenant_store:
            Optional custom storage backend (defaults to PostgreSQL).
        resolver:
            Optional custom resolver (defaults to strategy from config).
        isolation_provider:
            Optional custom isolation provider.
        skip_paths:
            URL prefixes that bypass tenant resolution (health checks, docs).
        debug_headers:
            When ``True``, adds ``X-Tenant-ID`` / ``X-Tenant-Identifier``
            response headers — useful during development.
        """
        from fastapi_tenancy.middleware.tenancy import TenancyMiddleware

        @asynccontextmanager
        async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
            manager = TenancyManager(
                config,
                tenant_store=tenant_store,
                resolver=resolver,
                isolation_provider=isolation_provider,
            )

            # ── Middleware registration ────────────────────────────────────
            # MUST happen before `yield`.  Starlette rebuilds the middleware
            # stack on first request; calling add_middleware after the stack
            # is built raises RuntimeError("Cannot add middleware after an
            # application has started").  The lifespan callable runs during
            # ASGI startup — after FastAPI is constructed but before it
            # processes any requests — so this is the correct place.
            app.add_middleware(
                TenancyMiddleware,
                config=config,
                manager=manager,   # resolver looked up via manager post-init
                skip_paths=skip_paths,
                debug_headers=debug_headers,
            )

            # ── Register app state so dependencies can access components ───
            app.state.tenancy_manager = manager
            app.state.tenancy_config = config

            # ── I/O initialisation ────────────────────────────────────────
            await manager.initialize()

            # ── Expose post-init state ────────────────────────────────────
            app.state.tenant_store = manager.tenant_store
            app.state.isolation_provider = manager.isolation_provider

            try:
                yield
            finally:
                await manager.shutdown()

        return _lifespan

    def _initialize_storage(self) -> None:
        if self._custom_store is not None:
            self.tenant_store = self._custom_store
        else:
            from fastapi_tenancy.storage.postgres import SQLAlchemyTenantStore
            self.tenant_store = SQLAlchemyTenantStore(
                database_url=str(self.config.database_url),
                pool_size=self.config.database_pool_size,
                max_overflow=self.config.database_max_overflow,
            )

    def _initialize_resolver(self) -> None:
        if self._custom_resolver is not None:
            self.resolver = self._custom_resolver
        else:
            from fastapi_tenancy.resolution.factory import ResolverFactory
            self.resolver = ResolverFactory.create(
                strategy=self.config.resolution_strategy,
                config=self.config,
                tenant_store=self.tenant_store,
            )

    def _initialize_isolation(self) -> None:
        if self._custom_isolation is not None:
            self.isolation_provider = self._custom_isolation
        else:
            from fastapi_tenancy.isolation.factory import IsolationProviderFactory
            self.isolation_provider = IsolationProviderFactory.create(
                strategy=self.config.isolation_strategy,
                config=self.config,
            )

    async def _create_default_tenants(self) -> None:
        """Seed a demo tenant when self-registration is enabled and no tenants exist.

        Does NOT call ``initialize_tenant`` — the caller is responsible for
        running migrations / creating schemas.  This only creates the store
        record so the system is not completely empty on first start.
        """
        if not self.config.allow_tenant_registration:
            return
        try:
            count = await self.tenant_store.count()
        except Exception as exc:
            logger.warning("Could not count tenants during seed: %s", exc)
            return
        if count > 0:
            return
        demo = Tenant(
            id="demo-tenant-001",
            identifier="demo",
            name="Demo Tenant",
            status=TenantStatus.ACTIVE,
            metadata={"demo": True},
        )
        try:
            await self.tenant_store.create(demo)
            logger.info("Created default demo tenant (store record only — run migrations separately)")  # noqa: E501
        except Exception as exc:
            logger.warning("Could not create demo tenant: %s", exc)

    @asynccontextmanager
    async def tenant_scope(self, tenant_id: str) -> AsyncIterator[Tenant]:
        """Async context manager that sets tenant context for *tenant_id*.

        Useful in background tasks, workers, and management commands::

            async with manager.tenant_scope("acme-corp-001") as tenant:
                await process_tenant_data(tenant)
        """
        tenant = await self.tenant_store.get_by_id(tenant_id)
        async with TenantContext.scope(tenant):
            yield tenant

    async def health_check(self) -> dict[str, Any]:
        """Return health information for all managed components."""
        health: dict[str, Any] = {"status": "healthy", "components": {}}
        try:
            count = await self.tenant_store.count()
            health["components"]["tenant_store"] = {
                "status": "healthy",
                "tenant_count": count,
            }
        except Exception as exc:
            health["status"] = "unhealthy"
            health["components"]["tenant_store"] = {
                "status": "unhealthy",
                "error": str(exc),
            }
        return health

    async def get_metrics(self) -> dict[str, Any]:
        """Return basic tenancy metrics, fetching all counts in parallel."""
        total, active, suspended = await asyncio.gather(
            self.tenant_store.count(),
            self.tenant_store.count(status=TenantStatus.ACTIVE),
            self.tenant_store.count(status=TenantStatus.SUSPENDED),
        )
        return {
            "total_tenants": total,
            "active_tenants": active,
            "suspended_tenants": suspended,
            "resolution_strategy": self.config.resolution_strategy.value,
            "isolation_strategy": self.config.isolation_strategy.value,
            "initialized": self._initialized,
        }


__all__ = ["TenancyManager"]
