"""Database-per-tenant isolation — multi-database compatible.

PostgreSQL
    CREATE DATABASE + per-connection engine pool.
MySQL / MariaDB
    CREATE DATABASE (SCHEMA synonym) — full support.
SQLite
    Per-tenant .db file at a configured path (sqlite:///./tenants/{slug}.db).
    Falls back to :class:`SchemaIsolationProvider` prefix mode if in-memory.
MSSQL / Other
    Raises ``IsolationError`` with a clear message — these require manual setup.

Security
--------
Every database name is validated via ``assert_safe_database_name()`` before
any DDL interpolation.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from fastapi_tenancy.core.exceptions import IsolationError
from fastapi_tenancy.isolation.base import BaseIsolationProvider
from fastapi_tenancy.utils.db_compat import (
    DbDialect,
    detect_dialect,
    requires_static_pool,
)
from fastapi_tenancy.utils.validation import assert_safe_database_name, sanitize_identifier

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy import MetaData

    from fastapi_tenancy.core.config import TenancyConfig
    from fastapi_tenancy.core.types import Tenant

logger = logging.getLogger(__name__)


class DatabaseIsolationProvider(BaseIsolationProvider):
    """Separate database per tenant — multi-database compatible.

    PostgreSQL / MySQL
    ~~~~~~~~~~~~~~~~~~
    Creates a new database via DDL (``CREATE DATABASE``).  Uses a master engine
    (pointing to the admin/default database) for DDL and per-tenant engines for
    queries.

    SQLite
    ~~~~~~
    Creates a per-tenant ``.db`` file by substituting the tenant slug into the
    base URL (e.g., ``sqlite+aiosqlite:///./data/{slug}.db``).  Each tenant
    gets an independent file-backed database.

    Example
    -------
    .. code-block:: python

        # PostgreSQL — creates tenant_acme_corp_db database
        provider = DatabaseIsolationProvider(config)
        await provider.initialize_tenant(tenant, metadata=Base.metadata)

        async with provider.get_session(tenant) as session:
            result = await session.execute(select(Order))

        # SQLite dev — creates ./data/acme_corp.db
        config = TenancyConfig(
            database_url="sqlite+aiosqlite:///./data/main.db",
            isolation_strategy="database",
        )
    """

    def __init__(
        self,
        config: TenancyConfig,
        master_engine: AsyncEngine | None = None,
    ) -> None:
        super().__init__(config)
        self.dialect = detect_dialect(str(config.database_url))
        self._engines: dict[str, AsyncEngine] = {}
        # Prevents two concurrent requests for the same new tenant from
        # each racing through _get_tenant_engine() and leaking an engine.
        self._engines_lock: asyncio.Lock = asyncio.Lock()

        if master_engine is not None:
            self._master_engine: AsyncEngine = master_engine
        else:
            kw: dict[str, Any] = {
                "echo": config.database_echo,
                "pool_pre_ping": not requires_static_pool(self.dialect),
            }
            if requires_static_pool(self.dialect):
                kw["poolclass"] = StaticPool
                kw["connect_args"] = {"check_same_thread": False}
            else:
                kw["pool_size"] = max(config.database_pool_size, 5)
                kw["max_overflow"] = config.database_max_overflow
                kw["pool_pre_ping"] = True
            kw["isolation_level"] = "AUTOCOMMIT"  # required for CREATE DATABASE

            self._master_engine = create_async_engine(str(config.database_url), **kw)

        logger.info(
            "DatabaseIsolationProvider dialect=%s", self.dialect.value
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_database_name(self, tenant: Tenant) -> str:
        slug = sanitize_identifier(tenant.identifier)
        return f"tenant_{slug}_db"

    def _build_tenant_url(self, tenant: Tenant) -> str:
        """Build the connection URL for a tenant's dedicated database."""
        base_url = str(self.config.database_url)
        slug = sanitize_identifier(tenant.identifier)  # always compute first
        db_name = self._get_database_name(tenant)

        if self.dialect == DbDialect.SQLITE:
            # Replace the file name component, preserving the scheme and path prefix.
            # sqlite+aiosqlite:///./data/main.db → sqlite+aiosqlite:///./data/acme_corp.db
            parts = base_url.rsplit("/", 1)
            if len(parts) == 2:
                return f"{parts[0]}/{slug}.db"
            return base_url

        if self.config.database_url_template:
            return self.config.database_url_template.format(
                tenant_id=tenant.id,
                database_name=db_name,
            )

        # PostgreSQL / MySQL: replace the database name at the end of the URL.
        import re as _re
        new_url = _re.sub(r"(/[^/?]*)(\?.*)?$", f"/{db_name}\\2", base_url)
        return new_url

    async def _get_tenant_engine(self, tenant: Tenant) -> AsyncEngine:
        # Fast path — engine already exists (no lock needed for read)
        if tenant.id in self._engines:
            return self._engines[tenant.id]

        # Slow path — first request for this tenant; acquire lock to prevent
        # a race where two concurrent callers both create engines and one leaks.
        async with self._engines_lock:
            # Re-check after acquiring lock (another coroutine may have just created it)
            if tenant.id in self._engines:
                return self._engines[tenant.id]

            url = self._build_tenant_url(tenant)
            kw: dict[str, Any] = {"echo": self.config.database_echo}
            if requires_static_pool(self.dialect):
                kw["poolclass"] = StaticPool
                kw["connect_args"] = {"check_same_thread": False}
            else:
                kw["pool_size"] = self.config.database_pool_size
                kw["max_overflow"] = self.config.database_max_overflow
                kw["pool_pre_ping"] = True
                kw["pool_recycle"] = self.config.database_pool_recycle

            engine = create_async_engine(url, **kw)
            self._engines[tenant.id] = engine
            logger.debug("Created engine for tenant %s url=%s", tenant.id, url)
            return engine

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def get_session(self, tenant: Tenant) -> AsyncIterator[AsyncSession]:
        engine = await self._get_tenant_engine(tenant)
        async with AsyncSession(engine, expire_on_commit=False) as session:
            try:
                yield session
            except Exception as exc:
                await session.rollback()
                raise IsolationError(
                    operation="get_session",
                    tenant_id=tenant.id,
                    details={"error": str(exc)},
                ) from exc

    async def apply_filters(self, query: Any, tenant: Tenant) -> Any:
        """Database isolation — no query modification needed."""
        return query

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize_tenant(
        self,
        tenant: Tenant,
        metadata: MetaData | None = None,
    ) -> None:
        """Create tenant database and optionally create tables."""
        if self.dialect == DbDialect.SQLITE:
            # SQLite: just create tables — file is auto-created
            engine = await self._get_tenant_engine(tenant)
            if metadata is not None:
                async with engine.begin() as conn:
                    await conn.run_sync(metadata.create_all)
            logger.info("SQLite tenant %s initialised", tenant.id)
            return

        if self.dialect == DbDialect.MSSQL:
            raise IsolationError(
                operation="initialize_tenant",
                tenant_id=tenant.id,
                details={
                    "reason": "DATABASE isolation on MSSQL requires manual database creation. "
                    "Use SCHEMA isolation or create the database manually.",
                },
            )

        db_name = self._get_database_name(tenant)
        try:
            assert_safe_database_name(db_name, context=f"tenant id={tenant.id!r}")
        except ValueError as exc:
            raise IsolationError(
                operation="initialize_tenant",
                tenant_id=tenant.id,
                details={"database": db_name, "error": str(exc)},
            ) from exc

        try:
            async with self._master_engine.connect() as conn:
                if self.dialect == DbDialect.POSTGRESQL:
                    result = await conn.execute(
                        text("SELECT 1 FROM pg_database WHERE datname = :dbname"),
                        {"dbname": db_name},
                    )
                    if result.scalar() is not None:
                        logger.warning("Database %r already exists", db_name)
                        return
                    await conn.execute(text(f'CREATE DATABASE "{db_name}"'))

                elif self.dialect == DbDialect.MYSQL:
                    await conn.execute(
                        text(f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")  # noqa: E501
                    )

                logger.info("Created database %r for tenant %s", db_name, tenant.id)

            if metadata is not None:
                engine = await self._get_tenant_engine(tenant)
                async with engine.begin() as conn:
                    await conn.run_sync(metadata.create_all)
                logger.info("Created tables in database %r", db_name)

        except IsolationError:
            raise
        except Exception as exc:
            logger.error("Failed to init tenant %s: %s", tenant.id, exc, exc_info=True)
            raise IsolationError(
                operation="initialize_tenant",
                tenant_id=tenant.id,
                details={"database": db_name, "error": str(exc)},
            ) from exc

    async def destroy_tenant(self, tenant: Tenant) -> None:
        """Drop tenant database.

        .. warning::
            Permanently destroys all data.
        """
        if self.dialect == DbDialect.SQLITE:
            import os
            engine = self._engines.pop(tenant.id, None)
            if engine:
                await engine.dispose()
            url = self._build_tenant_url(tenant)
            db_path = url.split("///", 1)[-1].lstrip("./")
            if db_path and os.path.exists(db_path):
                os.remove(db_path)
                logger.warning("Deleted SQLite file %s for tenant %s", db_path, tenant.id)
            return

        db_name = self._get_database_name(tenant)
        try:
            assert_safe_database_name(db_name, context=f"tenant id={tenant.id!r}")
        except ValueError as exc:
            raise IsolationError(
                operation="destroy_tenant",
                tenant_id=tenant.id,
                details={"database": db_name, "error": str(exc)},
            ) from exc

        if tenant.id in self._engines:
            await self._engines.pop(tenant.id).dispose()

        try:
            async with self._master_engine.connect() as conn:
                if self.dialect == DbDialect.POSTGRESQL:
                    await conn.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname = :dbname AND pid <> pg_backend_pid()"
                        ),
                        {"dbname": db_name},
                    )
                    await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
                elif self.dialect == DbDialect.MYSQL:
                    await conn.execute(text(f"DROP DATABASE IF EXISTS `{db_name}`"))
            logger.warning("Destroyed database %r for tenant %s", db_name, tenant.id)
        except IsolationError:
            raise
        except Exception as exc:
            raise IsolationError(
                operation="destroy_tenant",
                tenant_id=tenant.id,
                details={"database": db_name, "error": str(exc)},
            ) from exc

    async def verify_isolation(self, tenant: Tenant) -> bool:
        if self.dialect == DbDialect.SQLITE:
            url = self._build_tenant_url(tenant)
            import os
            path = url.split("///", 1)[-1].lstrip("./")
            return os.path.exists(path)

        db_name = self._get_database_name(tenant)
        try:
            assert_safe_database_name(db_name)
        except ValueError:
            return False

        try:
            async with self._master_engine.connect() as conn:
                if self.dialect == DbDialect.POSTGRESQL:
                    result = await conn.execute(
                        text("SELECT 1 FROM pg_database WHERE datname = :dbname"),
                        {"dbname": db_name},
                    )
                elif self.dialect == DbDialect.MYSQL:
                    result = await conn.execute(
                        text("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = :dbname"),  # noqa: E501
                        {"dbname": db_name},
                    )
                else:
                    return False
                return result.scalar() is not None
        except Exception:
            return False

    async def close(self) -> None:
        logger.info("Closing DatabaseIsolationProvider")
        for tid, engine in list(self._engines.items()):
            await engine.dispose()
            logger.debug("Closed engine tenant=%s", tid)
        self._engines.clear()
        await self._master_engine.dispose()
        logger.info("DatabaseIsolationProvider closed")


__all__ = ["DatabaseIsolationProvider"]
