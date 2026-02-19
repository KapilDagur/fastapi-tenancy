"""DatabaseIsolationProvider — SQLite file-per-tenant, URL building, factory."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastapi_tenancy.core.exceptions import IsolationError
from fastapi_tenancy.core.types import Tenant
from fastapi_tenancy.utils.db_compat import DbDialect


def make_tenant(slug: str = "acme-corp") -> Tenant:
    return Tenant(id=f"t-{slug}", identifier=slug, name=slug.title())


def make_sqlite_config(url: str = "sqlite+aiosqlite:///./test.db"):
    cfg = MagicMock()
    cfg.database_url = url
    cfg.database_pool_size = 1
    cfg.database_max_overflow = 0
    cfg.database_pool_timeout = 5
    cfg.database_pool_recycle = 600
    cfg.database_echo = False
    cfg.database_url_template = None
    return cfg


def make_pg_config():
    cfg = MagicMock()
    cfg.database_url = "postgresql+asyncpg://u:p@h/master"
    cfg.database_pool_size = 5
    cfg.database_max_overflow = 10
    cfg.database_pool_timeout = 30
    cfg.database_pool_recycle = 3600
    cfg.database_echo = False
    cfg.database_url_template = None
    return cfg


class TestDatabaseProviderInit:

    def test_sqlite_dialect_detected(self) -> None:
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        with patch("fastapi_tenancy.isolation.database.create_async_engine", return_value=MagicMock()):
            p = DatabaseIsolationProvider(make_sqlite_config())
        assert p.dialect == DbDialect.SQLITE

    def test_pg_dialect_detected(self) -> None:
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        with patch("fastapi_tenancy.isolation.database.create_async_engine", return_value=MagicMock()):
            p = DatabaseIsolationProvider(make_pg_config())
        assert p.dialect == DbDialect.POSTGRESQL

    def test_engines_dict_empty(self) -> None:
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        with patch("fastapi_tenancy.isolation.database.create_async_engine", return_value=MagicMock()):
            p = DatabaseIsolationProvider(make_sqlite_config())
        assert p._engines == {}


class TestBuildTenantUrl:

    def test_sqlite_replaces_file_part(self) -> None:
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        with patch("fastapi_tenancy.isolation.database.create_async_engine", return_value=MagicMock()):
            p = DatabaseIsolationProvider(make_sqlite_config("sqlite+aiosqlite:///./data/main.db"))
        t = make_tenant("my-tenant")
        url = p._build_tenant_url(t)
        assert "sqlite" in url
        assert "my_tenant" in url

    def test_sqlite_memory_url(self) -> None:
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        with patch("fastapi_tenancy.isolation.database.create_async_engine", return_value=MagicMock()):
            p = DatabaseIsolationProvider(make_sqlite_config("sqlite+aiosqlite:///:memory:"))
        t = make_tenant("acme-corp")
        url = p._build_tenant_url(t)
        assert "acme_corp" in url

    def test_pg_replaces_db_name(self) -> None:
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        with patch("fastapi_tenancy.isolation.database.create_async_engine", return_value=MagicMock()):
            p = DatabaseIsolationProvider(make_pg_config())
        t = make_tenant("acme-corp")
        url = p._build_tenant_url(t)
        assert "tenant_acme_corp_db" in url

    def test_url_template_used_when_set(self) -> None:
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        cfg = make_pg_config()
        cfg.database_url_template = "postgresql+asyncpg://u:p@h/{database_name}"
        with patch("fastapi_tenancy.isolation.database.create_async_engine", return_value=MagicMock()):
            p = DatabaseIsolationProvider(cfg)
        t = make_tenant("acme-corp")
        url = p._build_tenant_url(t)
        assert "tenant_acme_corp_db" in url


class TestGetDatabaseName:

    def test_database_name_format(self) -> None:
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        with patch("fastapi_tenancy.isolation.database.create_async_engine", return_value=MagicMock()):
            p = DatabaseIsolationProvider(make_sqlite_config())
        t = make_tenant("my-tenant")
        name = p._get_database_name(t)
        assert name == "tenant_my_tenant_db"


class TestInitializeAndDestroy:

    @pytest.mark.asyncio
    async def test_mssql_initialize_raises_isolation_error(self) -> None:
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        with patch("fastapi_tenancy.isolation.database.create_async_engine", return_value=MagicMock()):
            p = DatabaseIsolationProvider(make_sqlite_config("mssql+aioodbc://u:p@h/db"))
        with pytest.raises(IsolationError):
            await p.initialize_tenant(make_tenant())

    @pytest.mark.asyncio
    async def test_close_disposes_all_engines(self) -> None:
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        master = AsyncMock()
        master.dispose = AsyncMock()
        with patch("fastapi_tenancy.isolation.database.create_async_engine", return_value=master):
            p = DatabaseIsolationProvider(make_pg_config())
        tenant_eng = AsyncMock()
        tenant_eng.dispose = AsyncMock()
        p._engines["fake-id"] = tenant_eng
        await p.close()
        tenant_eng.dispose.assert_called_once()
        master.dispose.assert_called_once()
        assert len(p._engines) == 0

    @pytest.mark.asyncio
    async def test_apply_filters_passthrough(self) -> None:
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        with patch("fastapi_tenancy.isolation.database.create_async_engine", return_value=MagicMock()):
            p = DatabaseIsolationProvider(make_sqlite_config())
        t = make_tenant()
        raw = "SELECT 1"
        result = await p.apply_filters(raw, t)
        assert result == raw

    @pytest.mark.asyncio
    async def test_apply_filters_adds_where(self) -> None:
        import sqlalchemy as sa

        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        with patch("fastapi_tenancy.isolation.database.create_async_engine", return_value=MagicMock()):
            p = DatabaseIsolationProvider(make_sqlite_config())
        t = make_tenant("acme-corp")
        meta = sa.MetaData()
        tbl = sa.Table("orders", meta, sa.Column("tenant_id", sa.String))
        q = sa.select(tbl)
        filtered = await p.apply_filters(q, t)
        assert hasattr(filtered, "whereclause")


class TestHybridProvider:

    def test_hybrid_creates_both_sub_providers(self) -> None:
        from fastapi_tenancy.core.types import IsolationStrategy
        from fastapi_tenancy.isolation.hybrid import HybridIsolationProvider

        cfg = MagicMock()
        cfg.database_url = "sqlite+aiosqlite:///:memory:"
        cfg.database_pool_size = 1
        cfg.database_max_overflow = 0
        cfg.database_pool_timeout = 5
        cfg.database_pool_recycle = 600
        cfg.database_echo = False
        cfg.database_url_template = None
        cfg.premium_isolation_strategy = IsolationStrategy.RLS
        cfg.standard_isolation_strategy = IsolationStrategy.RLS

        with patch("fastapi_tenancy.isolation.rls.create_async_engine", return_value=MagicMock()), \
             patch("fastapi_tenancy.isolation.hybrid.create_async_engine", return_value=MagicMock()):
            p = HybridIsolationProvider(cfg)
        assert p.premium_provider is not None
        assert p.standard_provider is not None

    @pytest.mark.asyncio
    async def test_hybrid_close_closes_providers(self) -> None:
        from fastapi_tenancy.core.types import IsolationStrategy
        from fastapi_tenancy.isolation.hybrid import HybridIsolationProvider

        cfg = MagicMock()
        cfg.database_url = "sqlite+aiosqlite:///:memory:"
        cfg.database_pool_size = 1
        cfg.database_max_overflow = 0
        cfg.database_pool_timeout = 5
        cfg.database_pool_recycle = 600
        cfg.database_echo = False
        cfg.database_url_template = None
        cfg.premium_isolation_strategy = IsolationStrategy.RLS
        cfg.standard_isolation_strategy = IsolationStrategy.RLS

        with patch("fastapi_tenancy.isolation.rls.create_async_engine", return_value=MagicMock()), \
             patch("fastapi_tenancy.isolation.hybrid.create_async_engine", return_value=MagicMock()):
            p = HybridIsolationProvider(cfg)

        p.premium_provider = AsyncMock()
        p.premium_provider.close = AsyncMock()
        p.standard_provider = AsyncMock()
        p.standard_provider.close = AsyncMock()
        engine = AsyncMock()
        engine.dispose = AsyncMock()
        p._shared_engine = engine

        await p.close()
        p.premium_provider.close.assert_called_once()
        p.standard_provider.close.assert_called_once()
        engine.dispose.assert_called_once()
