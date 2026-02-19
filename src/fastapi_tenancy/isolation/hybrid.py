"""Hybrid isolation provider — routes tenants to different strategies by tier.

Fix from v0.1.0
---------------
When both premium and standard strategies target the same database (the most
common configuration: schema for premium, RLS for standard), the two providers
used to create separate connection pools to the same server, doubling resource
usage.

The fix: a single ``AsyncEngine`` is created here and injected into both
providers via ``engine=`` keyword arguments.  Providers that accept an
``engine`` parameter use it instead of building their own.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from fastapi_tenancy.core.types import IsolationStrategy
from fastapi_tenancy.isolation.base import BaseIsolationProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

    from fastapi_tenancy.core.config import TenancyConfig
    from fastapi_tenancy.core.types import Tenant

logger = logging.getLogger(__name__)


class HybridIsolationProvider(BaseIsolationProvider):
    """Route tenants to different isolation strategies based on their tier.

    Configuration example::

        config = TenancyConfig(
            isolation_strategy="hybrid",
            premium_isolation_strategy="schema",   # schema per premium tenant
            standard_isolation_strategy="rls",     # shared tables for standard
            premium_tenants=["acme-corp", "widgets-inc"],
        )

    The single shared ``AsyncEngine`` is reused across both sub-providers
    when they target the same database (avoiding duplicate connection pools).
    """

    def __init__(self, config: TenancyConfig) -> None:
        super().__init__(config)

        # Build one engine for the shared database and pass it to both providers
        # so they share the same connection pool.
        from sqlalchemy.pool import StaticPool as _StaticPool

        from fastapi_tenancy.utils.db_compat import detect_dialect, requires_static_pool

        _dialect = detect_dialect(str(config.database_url))
        _kw: dict[str, Any] = {"echo": config.database_echo}
        if requires_static_pool(_dialect):
            # SQLite in-memory: must use StaticPool to share the connection
            _kw["poolclass"] = _StaticPool
            _kw["connect_args"] = {"check_same_thread": False}
        else:
            _kw["pool_size"] = config.database_pool_size
            _kw["max_overflow"] = config.database_max_overflow
            _kw["pool_timeout"] = config.database_pool_timeout
            _kw["pool_recycle"] = config.database_pool_recycle
            _kw["pool_pre_ping"] = True

        self._shared_engine: AsyncEngine = create_async_engine(
            str(config.database_url), **_kw
        )

        self.premium_provider = self._create_provider(
            config.premium_isolation_strategy, self._shared_engine
        )
        self.standard_provider = self._create_provider(
            config.standard_isolation_strategy, self._shared_engine
        )

        logger.info(
            "HybridIsolationProvider initialised premium=%s standard=%s",
            config.premium_isolation_strategy.value,
            config.standard_isolation_strategy.value,
        )

    def _create_provider(
        self, strategy: IsolationStrategy, engine: AsyncEngine
    ) -> BaseIsolationProvider:
        """Instantiate a sub-provider, passing the shared engine directly.

        Providers that accept ``engine=`` use it instead of creating their own,
        so there is exactly one connection pool regardless of how many strategies
        share the same database.
        """
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        from fastapi_tenancy.isolation.rls import RLSIsolationProvider
        from fastapi_tenancy.isolation.schema import SchemaIsolationProvider

        if strategy == IsolationStrategy.SCHEMA:
            # Pass engine= so SchemaIsolationProvider skips its own pool creation
            return SchemaIsolationProvider(self.config, engine=engine)

        if strategy == IsolationStrategy.RLS:
            # Pass engine= so RLSIsolationProvider skips its own pool creation
            return RLSIsolationProvider(self.config, engine=engine)

        if strategy == IsolationStrategy.DATABASE:
            # DATABASE manages per-tenant engines itself; share the master/admin engine
            return DatabaseIsolationProvider(self.config, master_engine=engine)

        raise ValueError(
            f"Unsupported isolation strategy for HybridIsolationProvider: {strategy}"
        )

    def _get_provider(self, tenant: Tenant) -> BaseIsolationProvider:
        is_premium = self.config.is_premium_tenant(tenant.id)
        return self.premium_provider if is_premium else self.standard_provider

    @asynccontextmanager
    async def get_session(self, tenant: Tenant) -> AsyncIterator[AsyncSession]:
        async with self._get_provider(tenant).get_session(tenant) as session:
            yield session

    async def apply_filters(self, query: Any, tenant: Tenant) -> Any:
        return await self._get_provider(tenant).apply_filters(query, tenant)

    async def initialize_tenant(self, tenant: Tenant) -> None:
        await self._get_provider(tenant).initialize_tenant(tenant)

    async def destroy_tenant(self, tenant: Tenant) -> None:
        await self._get_provider(tenant).destroy_tenant(tenant)

    async def close(self) -> None:
        """Dispose the shared engine — automatically closes all sub-providers."""
        logger.info("Closing HybridIsolationProvider shared engine")
        await self._shared_engine.dispose()
        logger.info("HybridIsolationProvider closed")


__all__ = ["HybridIsolationProvider"]
