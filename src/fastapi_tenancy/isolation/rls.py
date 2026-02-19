"""Row-Level Security (RLS) isolation — multi-database compatible.

PostgreSQL
    Sets ``app.current_tenant`` session variable; RLS policies filter rows.
    Adds explicit ``WHERE tenant_id = :id`` as defence-in-depth.

MySQL / MariaDB
    Sets ``@current_tenant`` user-defined variable for stored-procedure use.
    Explicit ``WHERE tenant_id = :id`` filter applied on every query.

SQLite / Other dialects
    No session variable support.  Pure explicit-filter mode.
    Adds ``WHERE tenant_id = :id`` to every query via ``apply_filters()``.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from fastapi_tenancy.core.exceptions import IsolationError
from fastapi_tenancy.isolation.base import BaseIsolationProvider
from fastapi_tenancy.utils.db_compat import (
    detect_dialect,
    get_set_tenant_sql,
    requires_static_pool,
    supports_native_rls,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy import MetaData

    from fastapi_tenancy.core.config import TenancyConfig
    from fastapi_tenancy.core.types import Tenant

logger = logging.getLogger(__name__)


class RLSIsolationProvider(BaseIsolationProvider):
    """RLS-style isolation — gracefully degrades on non-PostgreSQL databases.

    On PostgreSQL the ``SET app.current_tenant`` call activates RLS policies
    configured on your tables.  On all other databases the provider falls back
    to explicit ``WHERE tenant_id = :id`` query filtering (via
    :meth:`apply_filters`) and logs a one-time warning.

    Example (PostgreSQL)
    --------------------
    .. code-block:: sql

        -- One-time DDL per table
        ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
        CREATE POLICY tenant_isolation ON orders
          USING (tenant_id = current_setting('app.current_tenant'));

    Example (SQLite / non-PG)
    --------------------------
    .. code-block:: python

        async with provider.get_session(tenant) as session:
            # apply_filters adds WHERE automatically
            q = await provider.apply_filters(select(Order), tenant)
            result = await session.execute(q)
    """

    def __init__(self, config: TenancyConfig, engine: AsyncEngine | None = None) -> None:
        super().__init__(config)
        self.dialect = detect_dialect(str(config.database_url))

        if not supports_native_rls(self.dialect):
            logger.warning(
                "RLSIsolationProvider: dialect %s does not support native RLS. "
                "Falling back to explicit WHERE tenant_id filter. "
                "Ensure apply_filters() is called on every query.",
                self.dialect.value,
            )

        if engine is not None:
            self.engine = engine
        else:
            kw: dict[str, Any] = {"echo": config.database_echo}
            if requires_static_pool(self.dialect):
                kw["poolclass"] = StaticPool
                kw["connect_args"] = {"check_same_thread": False}
            else:
                kw["pool_size"] = config.database_pool_size
                kw["max_overflow"] = config.database_max_overflow
                kw["pool_timeout"] = config.database_pool_timeout
                kw["pool_recycle"] = config.database_pool_recycle
                kw["pool_pre_ping"] = True

            self.engine = create_async_engine(str(config.database_url), **kw)

        logger.info(
            "RLSIsolationProvider dialect=%s native_rls=%s",
            self.dialect.value,
            supports_native_rls(self.dialect),
        )

    @asynccontextmanager
    async def get_session(self, tenant: Tenant) -> AsyncIterator[AsyncSession]:
        """Yield a session with tenant context configured.

        For PostgreSQL: sets ``app.current_tenant`` session variable.
        For MySQL: sets ``@current_tenant`` user variable.
        For others: attaches tenant_id to ``session.info`` for use by
        :meth:`apply_filters`.
        """
        set_sql = get_set_tenant_sql(self.dialect, tenant.id)

        async with AsyncSession(self.engine, expire_on_commit=False) as session:
            try:
                if set_sql:
                    await session.execute(
                        text(set_sql),
                        {"tenant_id": tenant.id},
                    )
                    logger.debug(
                        "Set tenant context tenant=%s dialect=%s",
                        tenant.id, self.dialect.value,
                    )
                else:
                    # Store tenant_id in session.info for apply_filters
                    session.info["tenant_id"] = tenant.id

                yield session

            except IsolationError:
                raise
            except Exception as exc:
                await session.rollback()
                logger.error(
                    "RLS session error tenant=%s: %s", tenant.id, exc, exc_info=True
                )
                raise IsolationError(
                    operation="get_session",
                    tenant_id=tenant.id,
                    details={"dialect": self.dialect.value, "error": str(exc)},
                ) from exc

    async def apply_filters(self, query: Any, tenant: Tenant) -> Any:
        """Apply explicit ``WHERE tenant_id = :tenant_id`` bound-parameter filter.

        Called as defence-in-depth for PostgreSQL RLS and as the primary
        isolation mechanism for all other dialects.

        Uses ``sqlalchemy.column()`` so the tenant ID is always passed as a
        proper bind parameter — never interpolated into the SQL string.

        If *query* is a SQLAlchemy Core/ORM construct with a ``.where()``
        method the filter is applied.  Raw ``text()`` queries are returned
        unchanged — callers must add ``.bindparams(tenant_id=...)`` themselves.
        """
        if hasattr(query, "where"):
            from sqlalchemy import column
            return query.where(column("tenant_id") == tenant.id)
        return query

    async def initialize_tenant(self, tenant: Tenant) -> None:
        """No structural initialisation needed for RLS / explicit-filter mode."""
        logger.info(
            "RLS/filter tenant %s ready (dialect=%s, native_rls=%s)",
            tenant.id, self.dialect.value, supports_native_rls(self.dialect),
        )

    async def destroy_tenant(
        self,
        tenant: Tenant,
        *,
        table_names: list[str] | None = None,
        metadata: MetaData | None = None,
    ) -> None:
        """Delete all rows belonging to *tenant* from the shared tables.

        RLS isolation shares tables across all tenants — rows are distinguished
        by a ``tenant_id`` column.  This method deletes those rows.

        You **must** supply either ``table_names`` or ``metadata`` so the
        provider knows which tables to purge.  Without at least one of these
        arguments the method raises :class:`~fastapi_tenancy.core.exceptions.IsolationError`
        rather than silently doing nothing.

        Parameters
        ----------
        tenant:
            The tenant whose data should be deleted.
        table_names:
            Explicit list of table names to purge (e.g. ``["users", "orders"]``).
        metadata:
            SQLAlchemy :class:`~sqlalchemy.MetaData`; all ``Table`` objects
            that have a ``tenant_id`` column will be purged.

        Raises
        ------
        IsolationError
            If no table information is provided, or if a DELETE fails.

        Example
        -------
        .. code-block:: python

            # Using table_names
            await provider.destroy_tenant(tenant, table_names=["users", "orders"])

            # Using ORM metadata
            from myapp.models import Base
            await provider.destroy_tenant(tenant, metadata=Base.metadata)
        """
        from sqlalchemy import text as sa_text

        from fastapi_tenancy.utils.validation import assert_safe_schema_name

        if table_names is None and metadata is None:
            raise IsolationError(
                operation="destroy_tenant",
                tenant_id=tenant.id,
                details={
                    "reason": (
                        "RLS destroy_tenant requires either table_names= or metadata= "
                        "to know which tables to purge.  Pass the application metadata "
                        "or an explicit list of table names."
                    )
                },
            )

        tables_to_purge: list[str] = list(table_names or [])

        if metadata is not None:
            for table in metadata.sorted_tables:
                if "tenant_id" in table.c:
                    tables_to_purge.append(table.name)

        if not tables_to_purge:
            logger.warning(
                "destroy_tenant called for tenant %s but no tables with "
                "tenant_id column were found — nothing deleted.",
                tenant.id,
            )
            return

        async with AsyncSession(self.engine, expire_on_commit=False) as session:
            try:
                for tbl_name in tables_to_purge:
                    # Use parameterised DELETE to avoid injection via table names.
                    # Table names cannot be parameterised in SQL, but they are
                    # validated above (either from metadata or caller-supplied).
                    # We still quote them defensively.
                    try:
                        assert_safe_schema_name(tbl_name, context="destroy_tenant")
                    except ValueError as exc:
                        raise IsolationError(
                            operation="destroy_tenant",
                            tenant_id=tenant.id,
                            details={"table": tbl_name, "error": str(exc)},
                        ) from exc

                    await session.execute(
                        sa_text(f'DELETE FROM "{tbl_name}" WHERE tenant_id = :tid'),  # noqa: S608
                        {"tid": tenant.id},
                    )
                    logger.info(
                        "Deleted rows from %r for tenant %s", tbl_name, tenant.id
                    )

                await session.commit()
                logger.warning(
                    "destroy_tenant completed for tenant %s: purged %d tables %s",
                    tenant.id,
                    len(tables_to_purge),
                    tables_to_purge,
                )
            except IsolationError:
                await session.rollback()
                raise
            except Exception as exc:
                await session.rollback()
                raise IsolationError(
                    operation="destroy_tenant",
                    tenant_id=tenant.id,
                    details={"tables": tables_to_purge, "error": str(exc)},
                ) from exc

    async def close(self) -> None:
        logger.info("Closing RLSIsolationProvider engine")
        await self.engine.dispose()


__all__ = ["RLSIsolationProvider"]
