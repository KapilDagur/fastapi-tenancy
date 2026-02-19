"""SQLAlchemy-backed tenant storage — multi-database compatible.

Works with any async SQLAlchemy dialect:
- PostgreSQL + asyncpg (production)
- SQLite + aiosqlite (development / CI)
- MySQL + aiomysql (alternative production)

The ``Base`` and ``TenantModel`` use pure SQLAlchemy 2.0 ``Mapped[T]`` syntax
and do not use any database-specific column types, making them portable across
PostgreSQL, SQLite, MySQL, and other async-capable databases.
"""
from __future__ import annotations

import json
import logging
import warnings as _warnings
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, String, Text, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import StaticPool

from fastapi_tenancy.core.exceptions import TenancyError, TenantNotFoundError
from fastapi_tenancy.core.types import Tenant, TenantStatus
from fastapi_tenancy.storage.tenant_store import TenantStore
from fastapi_tenancy.utils.db_compat import detect_dialect, requires_static_pool

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class TenantModel(Base):
    """SQLAlchemy 2.0 ORM model for the ``tenants`` table.

    Uses ``Mapped[T]`` annotations and ``func.now()`` server defaults —
    no deprecated ``Column()`` style or sync-driver assumptions.
    """

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(255), primary_key=True, index=True)
    identifier: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active", index=True)
    isolation_strategy: Mapped[str | None] = mapped_column(String(50), nullable=True)
    database_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    schema_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # metadata_ stored as JSON text — portable across dialects
    metadata_json: Mapped[str] = mapped_column(
        "metadata", Text, nullable=False, default="{}", server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def to_domain(self) -> Tenant:
        try:
            meta: dict[str, Any] = json.loads(self.metadata_json or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        return Tenant(
            id=self.id,
            identifier=self.identifier,
            name=self.name,
            status=TenantStatus(self.status),
            isolation_strategy=self.isolation_strategy,
            metadata=meta,
            database_url=self.database_url,
            schema_name=self.schema_name,
            created_at=self.created_at or datetime.now(UTC),
            updated_at=self.updated_at or datetime.now(UTC),
        )


class SQLAlchemyTenantStore(TenantStore):
    """SQLAlchemy async tenant store — works with PostgreSQL, SQLite, MySQL.

    Despite the name (kept for backward compatibility), this store works
    with any async SQLAlchemy driver.

    Example (PostgreSQL)
    --------------------
    .. code-block:: python

        store = PostgreSQLTenantStore(
            database_url="postgresql+asyncpg://user:pass@localhost/myapp",
        )
        await store.initialize()

    Example (SQLite for testing)
    ----------------------------
    .. code-block:: python

        store = PostgreSQLTenantStore(
            database_url="sqlite+aiosqlite:///./test.db",
            pool_size=1,
        )
        await store.initialize()
    """

    def __init__(
        self,
        database_url: str,
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_pre_ping: bool = True,
        echo: bool = False,
    ) -> None:
        dialect = detect_dialect(database_url)
        kw: dict[str, Any] = {"echo": echo}

        if requires_static_pool(dialect):
            kw["poolclass"] = StaticPool
            kw["connect_args"] = {"check_same_thread": False}
        else:
            kw["pool_size"] = pool_size
            kw["max_overflow"] = max_overflow
            kw["pool_pre_ping"] = pool_pre_ping
            kw["pool_recycle"] = 3600

        self.engine: AsyncEngine = create_async_engine(database_url, **kw)
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
        logger.info(
            "SQLAlchemyTenantStore dialect=%s pool_size=%d",
            dialect.value, pool_size,
        )

    async def initialize(self) -> None:
        """Create tenants table if not exists (idempotent)."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Tenants table ready")

    async def get_by_id(self, tenant_id: str) -> Tenant:
        async with self.session_factory() as session:
            result = await session.execute(
                select(TenantModel).where(TenantModel.id == tenant_id)
            )
            model = result.scalar_one_or_none()
            if model is None:
                raise TenantNotFoundError(identifier=tenant_id)
            return model.to_domain()

    async def get_by_identifier(self, identifier: str) -> Tenant:
        async with self.session_factory() as session:
            result = await session.execute(
                select(TenantModel).where(TenantModel.identifier == identifier)
            )
            model = result.scalar_one_or_none()
            if model is None:
                raise TenantNotFoundError(identifier=identifier)
            return model.to_domain()

    async def create(self, tenant: Tenant) -> Tenant:
        async with self.session_factory() as session:
            model = TenantModel(
                id=tenant.id,
                identifier=tenant.identifier,
                name=tenant.name,
                status=tenant.status.value,
                isolation_strategy=(
                    tenant.isolation_strategy.value if tenant.isolation_strategy else None
                ),
                database_url=tenant.database_url,
                schema_name=tenant.schema_name,
                metadata_json=json.dumps(tenant.metadata),
                created_at=tenant.created_at,
                updated_at=tenant.updated_at,
            )
            session.add(model)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise ValueError(  # noqa: B904
                    f"Tenant id={tenant.id!r} or identifier={tenant.identifier!r} already exists."
                )
            except Exception as exc:
                await session.rollback()
                raise TenancyError(f"Failed to create tenant: {exc}") from exc
            await session.refresh(model)
            logger.info("Created tenant id=%s", tenant.id)
            return model.to_domain()

    async def update(self, tenant: Tenant) -> Tenant:
        async with self.session_factory() as session:
            try:
                result = await session.execute(
                    select(TenantModel).where(TenantModel.id == tenant.id)
                )
                model = result.scalar_one_or_none()
                if model is None:
                    raise TenantNotFoundError(identifier=tenant.id)
                model.identifier = tenant.identifier
                model.name = tenant.name
                model.status = tenant.status.value
                model.isolation_strategy = (
                    tenant.isolation_strategy.value if tenant.isolation_strategy else None
                )
                model.database_url = tenant.database_url
                model.schema_name = tenant.schema_name
                model.metadata_json = json.dumps(tenant.metadata)
                model.updated_at = datetime.now(UTC)
                await session.commit()
                await session.refresh(model)
                return model.to_domain()
            except TenantNotFoundError:
                raise
            except Exception as exc:
                await session.rollback()
                raise TenancyError(f"Failed to update tenant: {exc}") from exc

    async def delete(self, tenant_id: str) -> None:
        async with self.session_factory() as session:
            try:
                result = await session.execute(
                    select(TenantModel).where(TenantModel.id == tenant_id)
                )
                model = result.scalar_one_or_none()
                if model is None:
                    raise TenantNotFoundError(identifier=tenant_id)
                await session.delete(model)
                await session.commit()
            except TenantNotFoundError:
                raise
            except Exception as exc:
                await session.rollback()
                raise TenancyError(f"Failed to delete tenant: {exc}") from exc

    async def list(
        self,
        skip: int = 0,
        limit: int = 100,
        status: TenantStatus | None = None,
    ) -> list[Tenant]:
        async with self.session_factory() as session:
            query = select(TenantModel)
            if status is not None:
                query = query.where(TenantModel.status == status.value)
            query = query.offset(skip).limit(limit).order_by(TenantModel.created_at.desc())
            result = await session.execute(query)
            return [m.to_domain() for m in result.scalars().all()]

    async def count(self, status: TenantStatus | None = None) -> int:
        async with self.session_factory() as session:
            query = select(func.count(TenantModel.id))
            if status is not None:
                query = query.where(TenantModel.status == status.value)
            result = await session.execute(query)
            return result.scalar() or 0

    async def exists(self, tenant_id: str) -> bool:
        async with self.session_factory() as session:
            result = await session.execute(
                select(TenantModel.id).where(TenantModel.id == tenant_id)
            )
            return result.scalar_one_or_none() is not None

    async def set_status(self, tenant_id: str, status: TenantStatus) -> Tenant:
        async with self.session_factory() as session:
            try:
                result = await session.execute(
                    select(TenantModel).where(TenantModel.id == tenant_id)
                )
                model = result.scalar_one_or_none()
                if model is None:
                    raise TenantNotFoundError(identifier=tenant_id)
                model.status = status.value
                model.updated_at = datetime.now(UTC)
                await session.commit()
                await session.refresh(model)
                return model.to_domain()
            except TenantNotFoundError:
                raise
            except Exception as exc:
                await session.rollback()
                raise TenancyError(f"Failed to update status: {exc}") from exc

    async def update_metadata(
        self, tenant_id: str, metadata: dict[str, Any]
    ) -> Tenant:
        async with self.session_factory() as session:
            try:
                result = await session.execute(
                    select(TenantModel).where(TenantModel.id == tenant_id)
                )
                model = result.scalar_one_or_none()
                if model is None:
                    raise TenantNotFoundError(identifier=tenant_id)
                existing = json.loads(model.metadata_json or "{}")
                merged = {**existing, **metadata}
                model.metadata_json = json.dumps(merged)
                model.updated_at = datetime.now(UTC)
                await session.commit()
                await session.refresh(model)
                return model.to_domain()
            except TenantNotFoundError:
                raise
            except Exception as exc:
                await session.rollback()
                raise TenancyError(f"Failed to update metadata: {exc}") from exc

    async def close(self) -> None:
        await self.engine.dispose()
        logger.info("SQLAlchemyTenantStore closed")


class PostgreSQLTenantStore(SQLAlchemyTenantStore):
    """Deprecated alias for :class:`SQLAlchemyTenantStore`.

    .. deprecated:: 0.2.0
        Use :class:`SQLAlchemyTenantStore` instead.  This alias will be
        removed in v1.0.  ``PostgreSQLTenantStore`` is a misleading name —
        the store works with any async SQLAlchemy dialect (PostgreSQL, SQLite,
        MySQL).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _warnings.warn(
            "PostgreSQLTenantStore is deprecated and will be removed in v1.0. "
            "Use SQLAlchemyTenantStore instead — it works with all async SQLAlchemy dialects.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(*args, **kwargs)


__all__ = ["Base", "PostgreSQLTenantStore", "SQLAlchemyTenantStore", "TenantModel"]
