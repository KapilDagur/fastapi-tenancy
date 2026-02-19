"""Schema-per-tenant isolation provider — multi-database compatible.

Strategy selection
------------------
The provider auto-detects the database dialect on construction and selects
the appropriate isolation mechanism:

- **PostgreSQL / MSSQL** — native ``CREATE SCHEMA`` + ``SET search_path``
- **SQLite** — table-name prefix (``t_<tenant>_<table>``); no DDL schemas
- **MySQL / MariaDB** — per-tenant database (delegates to DatabaseIsolationProvider)
- **Unknown dialects** — table-name prefix fallback

Security
--------
Every schema / database name is validated with ``assert_safe_schema_name()``
before being interpolated into any DDL statement.  SQLite and prefix-mode
paths are injection-safe by construction (only validated identifiers are used
in Python string formatting, never raw DDL).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from fastapi_tenancy.core.exceptions import IsolationError
from fastapi_tenancy.isolation.base import BaseIsolationProvider
from fastapi_tenancy.utils.db_compat import (
    DbDialect,
    detect_dialect,
    make_table_prefix,
    requires_static_pool,
    supports_native_schemas,
)
from fastapi_tenancy.utils.validation import assert_safe_schema_name

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy import MetaData

    from fastapi_tenancy.core.config import TenancyConfig
    from fastapi_tenancy.core.types import Tenant

logger = logging.getLogger(__name__)


class SchemaIsolationProvider(BaseIsolationProvider):
    """Schema-per-tenant isolation — works across multiple database dialects.

    PostgreSQL / MSSQL
    ~~~~~~~~~~~~~~~~~~
    Creates a dedicated schema for each tenant and sets ``search_path`` on
    every session so unqualified table references resolve to the tenant schema.

    SQLite / Unknown dialects
    ~~~~~~~~~~~~~~~~~~~~~~~~~
    SQLite has no schema concept.  Isolation is provided via a *table-name
    prefix* (``t_<slug>_``).  The app's ``MetaData`` must be created with
    ``schema=None`` and the prefix applied via ``Table.__table_args__`` or by
    calling :meth:`initialize_tenant` which renames tables in a copy of the
    metadata.

    MySQL / MariaDB
    ~~~~~~~~~~~~~~~
    In MySQL, ``SCHEMA`` is a synonym for ``DATABASE``.  This provider
    transparently delegates to :class:`~fastapi_tenancy.isolation.database.\
DatabaseIsolationProvider` so callers don't need to know the difference.

    Example — PostgreSQL
    ---------------------
    .. code-block:: python

        provider = SchemaIsolationProvider(config)
        await provider.initialize_tenant(tenant, metadata=Base.metadata)

        async with provider.get_session(tenant) as session:
            result = await session.execute(select(User))

    Example — SQLite (development)
    --------------------------------
    .. code-block:: python

        config = TenancyConfig(
            database_url="sqlite+aiosqlite:///./test.db",
            ...
        )
        provider = SchemaIsolationProvider(config)
        prefix = provider.get_table_prefix(tenant)
        # "t_acme_corp_"
    """

    def __init__(self, config: TenancyConfig, engine: AsyncEngine | None = None) -> None:
        super().__init__(config)
        self.dialect = detect_dialect(str(config.database_url))
        logger.info(
            "%s dialect=%s native_schemas=%s",
            self.__class__.__name__,
            self.dialect.value,
            supports_native_schemas(self.dialect),
        )

        if engine is not None:
            self.engine = engine
        else:
            kw: dict[str, Any] = {
                "echo": config.database_echo,
                "pool_pre_ping": not requires_static_pool(self.dialect),
            }
            if requires_static_pool(self.dialect):
                # SQLite in-memory needs a shared connection
                kw["poolclass"] = StaticPool
                kw["connect_args"] = {"check_same_thread": False}
            else:
                kw["pool_size"] = config.database_pool_size
                kw["max_overflow"] = config.database_max_overflow
                kw["pool_timeout"] = config.database_pool_timeout
                kw["pool_recycle"] = config.database_pool_recycle

            self.engine = create_async_engine(str(config.database_url), **kw)

    def get_table_prefix(self, tenant: Tenant) -> str:
        """Return the table-name prefix for *tenant* (non-schema dialects)."""
        return make_table_prefix(tenant.identifier)

    def get_schema_name(self, tenant: Tenant) -> str:
        """Return the raw schema name string (before validation).

        Override or patch this in tests to inject arbitrary schema names.
        Validation happens in :meth:`_get_schema_name` before DDL.
        """
        return (
            tenant.schema_name
            if tenant.schema_name
            else self.config.get_schema_name(tenant.identifier)
        )

    def _get_schema_name(self, tenant: Tenant) -> str:
        """Return validated schema name (native-schema dialects only).

        Raises :class:`~fastapi_tenancy.core.exceptions.IsolationError` if
        the name is unsafe.
        """
        schema = self.get_schema_name(tenant)
        try:
            assert_safe_schema_name(schema, context=f"tenant id={tenant.id!r}")
        except ValueError as exc:
            raise IsolationError(
                operation="validate_schema_name",
                tenant_id=tenant.id,
                details={"schema": schema, "error": str(exc)},
            ) from exc
        return schema

    @asynccontextmanager
    async def get_session(self, tenant: Tenant) -> AsyncIterator[AsyncSession]:
        """Yield a tenant-scoped ``AsyncSession``.

        For native-schema dialects, ``search_path`` is set at session open.
        For prefix-based dialects, no session-level change is needed —
        isolation is enforced at the application/ORM layer via table prefixes.
        """
        if self.dialect == DbDialect.MYSQL:
            # Delegate to the database-per-tenant strategy
            async with self._mysql_get_session(tenant) as session:
                yield session
            return

        if supports_native_schemas(self.dialect):
            async with self._schema_get_session(tenant) as session:
                yield session
        else:
            # Prefix mode — plain session, no path manipulation
            async with self._prefix_get_session(tenant) as session:
                yield session

    @asynccontextmanager
    async def _schema_get_session(self, tenant: Tenant) -> AsyncIterator[AsyncSession]:
        schema_name = self._get_schema_name(tenant)
        async with AsyncSession(self.engine, expire_on_commit=False) as session:
            try:
                await session.execute(
                    text("SET search_path TO :schema, public").bindparams(schema=schema_name)
                )
                logger.debug("search_path → %r (tenant %s)", schema_name, tenant.id)
                yield session
            except IsolationError:
                raise
            except Exception as exc:
                await session.rollback()
                raise IsolationError(
                    operation="get_session",
                    tenant_id=tenant.id,
                    details={"schema": schema_name, "error": str(exc)},
                ) from exc

    @asynccontextmanager
    async def _prefix_get_session(self, tenant: Tenant) -> AsyncIterator[AsyncSession]:
        """Session for SQLite / unknown dialects — no path manipulation."""
        async with AsyncSession(self.engine, expire_on_commit=False) as session:
            try:
                # Attach prefix hint to session for ORM event subscribers
                session.info["tenant_id"] = tenant.id
                session.info["table_prefix"] = self.get_table_prefix(tenant)
                yield session
            except Exception as exc:
                await session.rollback()
                raise IsolationError(
                    operation="get_session",
                    tenant_id=tenant.id,
                    details={"mode": "prefix", "error": str(exc)},
                ) from exc

    @asynccontextmanager
    async def _mysql_get_session(self, tenant: Tenant) -> AsyncIterator[AsyncSession]:
        """Delegate to DatabaseIsolationProvider for MySQL."""
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        db_provider = DatabaseIsolationProvider(self.config, master_engine=self.engine)
        async with db_provider.get_session(tenant) as session:
            yield session

    async def initialize_tenant(
        self,
        tenant: Tenant,
        metadata: MetaData | None = None,
    ) -> None:
        """Create the tenant's isolation namespace.

        * **Native schemas** — runs ``CREATE SCHEMA IF NOT EXISTS``
        * **Prefix mode** — creates tables with the tenant prefix applied to
          all table names in *metadata* (if supplied)
        * **MySQL** — creates a per-tenant database

        Parameters
        ----------
        tenant:
            Target tenant.
        metadata:
            Application :class:`~sqlalchemy.MetaData`.  When provided,
            ``create_all()`` is executed in the tenant namespace.
        """
        if self.dialect == DbDialect.MYSQL:
            from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
            db_provider = DatabaseIsolationProvider(self.config, master_engine=self.engine)
            await db_provider.initialize_tenant(tenant, metadata=metadata)
            return

        if supports_native_schemas(self.dialect):
            await self._initialize_schema(tenant, metadata)
        else:
            await self._initialize_prefix(tenant, metadata)

    async def _initialize_schema(
        self, tenant: Tenant, metadata: MetaData | None
    ) -> None:
        # _get_schema_name() validates via assert_safe_schema_name() internally.
        # Explicit re-assertion here makes the guard visible at the DDL call site
        # for static analysis and code review clarity.
        schema_name = self._get_schema_name(tenant)
        assert_safe_schema_name(schema_name, context=f"initialize_schema tenant={tenant.id!r}")
        async with self.engine.begin() as conn:
            try:
                await conn.execute(
                    text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')
                )
                logger.info("Created schema %r for tenant %s", schema_name, tenant.id)
                if metadata is not None:
                    await conn.execute(
                        text("SET search_path TO :schema, public").bindparams(schema=schema_name)
                    )
                    await conn.run_sync(metadata.create_all)
                    logger.info("Created tables in schema %r", schema_name)
            except IsolationError:
                raise
            except Exception as exc:
                # Best-effort cleanup
                with suppress(Exception):
                    await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
                raise IsolationError(
                    operation="initialize_tenant",
                    tenant_id=tenant.id,
                    details={"schema": schema_name, "error": str(exc)},
                ) from exc

    async def _initialize_prefix(
        self, tenant: Tenant, metadata: MetaData | None
    ) -> None:
        """Create tables with tenant prefix for SQLite / unknown dialects.

        Uses ``Table.to_metadata()`` to copy table definitions — including
        all constraints, indexes, and foreign keys — into a new ``MetaData``
        with prefixed names.  Naïve column-by-column rebuilding (the old
        approach) silently dropped all constraints.

        Foreign keys between prefixed tables are remapped so they refer to
        the prefixed target table names rather than the originals.
        """
        prefix = self.get_table_prefix(tenant)
        logger.info(
            "Prefix-mode init for tenant %s (prefix=%r dialect=%s)",
            tenant.id, prefix, self.dialect.value,
        )
        if metadata is None:
            logger.info(
                "No metadata supplied — skipping table creation. "
                "Pass metadata= to have tables created with the prefix applied."
            )
            return

        import sqlalchemy as sa

        # Step 1 — build a name-mapping for FK remapping before we copy tables.
        #   original name → prefixed name
        name_map = {tbl.name: f"{prefix}{tbl.name}" for tbl in metadata.sorted_tables}

        # Step 2 — copy every table into a fresh MetaData using to_metadata().
        #   This preserves Column types, PK, unique/check constraints, indexes.
        #   We pass ``referred_schema=None`` to keep everything schema-less.
        prefixed_meta = sa.MetaData()
        for table in metadata.sorted_tables:
            new_name = name_map[table.name]
            table.to_metadata(prefixed_meta, name=new_name)

        # Step 3 — remap any ForeignKey column targets from original → prefixed names.
        for table in prefixed_meta.sorted_tables:
            for col in table.columns:
                for fk in list(col.foreign_keys):
                    # fk.target_fullname is "table_name.col_name"
                    parts = fk.target_fullname.split(".")
                    if len(parts) == 2:
                        orig_tbl, col_name = parts
                        if orig_tbl in name_map:
                            # Rebuild the FK pointing at the prefixed table
                            new_target = f"{name_map[orig_tbl]}.{col_name}"
                            col.foreign_keys.discard(fk)
                            col.append_foreign_key(sa.ForeignKey(new_target))

        async with self.engine.begin() as conn:
            try:
                await conn.run_sync(prefixed_meta.create_all)
                logger.info(
                    "Created %d prefixed tables for tenant %s",
                    len(prefixed_meta.sorted_tables), tenant.id,
                )
            except Exception as exc:
                raise IsolationError(
                    operation="initialize_tenant",
                    tenant_id=tenant.id,
                    details={"prefix": prefix, "error": str(exc)},
                ) from exc

    async def destroy_tenant(self, tenant: Tenant) -> None:
        """Drop the tenant's isolation namespace.

        .. warning::
            This permanently destroys all tenant data.
        """
        if self.dialect == DbDialect.MYSQL:
            from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
            db_provider = DatabaseIsolationProvider(self.config, master_engine=self.engine)
            await db_provider.destroy_tenant(tenant)
            return

        if supports_native_schemas(self.dialect):
            # _get_schema_name() validates; explicit assertion for call-site clarity.
            schema_name = self._get_schema_name(tenant)
            assert_safe_schema_name(schema_name, context=f"destroy_tenant tenant={tenant.id!r}")
            async with self.engine.begin() as conn:
                try:
                    await conn.execute(
                        text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')
                    )
                    logger.warning("Destroyed schema %r for tenant %s", schema_name, tenant.id)
                except Exception as exc:
                    raise IsolationError(
                        operation="destroy_tenant",
                        tenant_id=tenant.id,
                        details={"schema": schema_name, "error": str(exc)},
                    ) from exc
        else:
            # Prefix mode — drop all prefixed tables
            await self._destroy_prefix(tenant)

    async def _destroy_prefix(self, tenant: Tenant) -> None:
        prefix = self.get_table_prefix(tenant)
        async with self.engine.begin() as conn:
            try:
                # Inspect tables and drop those matching the prefix
                from sqlalchemy import inspect as sa_inspect
                def _drop(sync_conn: Any) -> None:
                    insp = sa_inspect(sync_conn)
                    tables = [
                        t for t in insp.get_table_names()
                        if t.startswith(prefix)
                    ]
                    # Validate every table name before DDL interpolation.
                    # get_table_names() returns DB-side values, but we guard
                    # defensively against any malicious name that was stored.
                    for tbl in tables:
                        assert_safe_schema_name(tbl, context=f"prefix table drop tenant={tenant.id!r}")  # noqa: E501
                        sync_conn.execute(
                            text(f'DROP TABLE IF EXISTS "{tbl}"')
                        )
                    logger.warning(
                        "Destroyed %d prefixed tables for tenant %s", len(tables), tenant.id
                    )
                await conn.run_sync(_drop)
            except Exception as exc:
                raise IsolationError(
                    operation="destroy_tenant",
                    tenant_id=tenant.id,
                    details={"prefix": prefix, "error": str(exc)},
                ) from exc

    async def verify_isolation(self, tenant: Tenant) -> bool:
        if supports_native_schemas(self.dialect):
            schema_name = self._get_schema_name(tenant)
            try:
                async with self.engine.connect() as conn:
                    result = await conn.execute(
                        text(
                            "SELECT schema_name FROM information_schema.schemata "
                            "WHERE schema_name = :name"
                        ),
                        {"name": schema_name},
                    )
                    return result.scalar() is not None
            except Exception:
                return False
        # Prefix mode — just check engine is alive
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def apply_filters(self, query: Any, tenant: Tenant) -> Any:
        """Apply tenant filter as defence-in-depth.

        For native-schema dialects ``search_path`` already isolates data;
        this filter is an additional safety net.
        For prefix-mode dialects (SQLite) this is the primary isolation.

        Uses ``sqlalchemy.column()`` with a bound parameter — never string
        interpolation, safe against SQL injection.
        """
        if hasattr(query, "where"):
            from sqlalchemy import column
            return query.where(column("tenant_id") == tenant.id)
        return query


    async def close(self) -> None:
        logger.info("Closing SchemaIsolationProvider engine")
        await self.engine.dispose()


__all__ = ["SchemaIsolationProvider"]
