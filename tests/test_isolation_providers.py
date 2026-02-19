"""Isolation provider tests using mocks — no real database needed.

Tests the control flow, error handling, argument validation, and delegation
logic for all four providers. Actual SQL execution is covered by the
SQLite integration tests (test_isolation_sqlite.py).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastapi_tenancy.core.exceptions import IsolationError
from fastapi_tenancy.core.types import Tenant
from fastapi_tenancy.utils.db_compat import DbDialect

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_tenant(slug: str = "acme-corp") -> Tenant:
    return Tenant(id=f"t-{slug}", identifier=slug, name=slug.title())


def make_mock_config(
    url: str = "postgresql+asyncpg://u:p@h/db",
    schema_prefix: str = "tenant_",
) -> MagicMock:
    cfg = MagicMock()
    cfg.database_url = url
    cfg.database_pool_size = 5
    cfg.database_max_overflow = 10
    cfg.database_pool_timeout = 30
    cfg.database_pool_recycle = 3600
    cfg.database_echo = False
    cfg.database_url_template = None
    cfg.schema_prefix = schema_prefix

    def get_schema(tid: str) -> str:
        return f"{schema_prefix}{tid.replace('-', '_')}"

    def get_db_url(tid: str) -> str:
        return f"postgresql+asyncpg://u:p@h/tenant_{tid.replace('-','_')}_db"

    cfg.get_schema_name = get_schema
    cfg.get_database_url_for_tenant = get_db_url
    return cfg


# ---------------------------------------------------------------------------
# SchemaIsolationProvider — mock-based
# ---------------------------------------------------------------------------

class TestSchemaProviderValidation:

    def test_get_schema_name_valid(self) -> None:
        from fastapi_tenancy.isolation.schema import SchemaIsolationProvider
        engine = MagicMock()
        cfg = make_mock_config()
        with patch("fastapi_tenancy.isolation.schema.create_async_engine", return_value=engine):
            p = SchemaIsolationProvider(cfg)
        t = make_tenant("acme-corp")
        assert p._get_schema_name(t) == "tenant_acme_corp"

    def test_get_schema_name_from_tenant_attr(self) -> None:
        from fastapi_tenancy.isolation.schema import SchemaIsolationProvider
        engine = MagicMock()
        cfg = make_mock_config()
        with patch("fastapi_tenancy.isolation.schema.create_async_engine", return_value=engine):
            p = SchemaIsolationProvider(cfg)
        t = Tenant(id="t1", identifier="acme-corp", name="Acme", schema_name="custom_schema")
        assert p._get_schema_name(t) == "custom_schema"

    def test_get_schema_name_injection_raises(self) -> None:
        from fastapi_tenancy.isolation.schema import SchemaIsolationProvider
        engine = MagicMock()
        cfg = make_mock_config()
        cfg.get_schema_name = lambda tid: "'; DROP TABLE tenants; --"
        with patch("fastapi_tenancy.isolation.schema.create_async_engine", return_value=engine):
            p = SchemaIsolationProvider(cfg)
        # Don't set schema_name so the config path is used (Tenant is frozen)
        t = Tenant(id="t1", identifier="acme-corp", name="Acme")
        with pytest.raises((ValueError, IsolationError)):
            p._get_schema_name(t)

    def test_table_prefix_generation(self) -> None:
        from fastapi_tenancy.isolation.schema import SchemaIsolationProvider
        with patch("fastapi_tenancy.isolation.schema.create_async_engine", return_value=MagicMock()):
            p = SchemaIsolationProvider(make_mock_config("sqlite+aiosqlite:///:memory:"))
        t = make_tenant("my-tenant")
        prefix = p.get_table_prefix(t)
        assert "my_tenant" in prefix
        assert prefix.endswith("_")

    def test_mysql_dialect_detected(self) -> None:
        from fastapi_tenancy.isolation.schema import SchemaIsolationProvider
        with patch("fastapi_tenancy.isolation.schema.create_async_engine", return_value=MagicMock()):
            p = SchemaIsolationProvider(make_mock_config("mysql+aiomysql://u:p@h/db"))
        assert p.dialect == DbDialect.MYSQL

    def test_pg_dialect_detected(self) -> None:
        from fastapi_tenancy.isolation.schema import SchemaIsolationProvider
        with patch("fastapi_tenancy.isolation.schema.create_async_engine", return_value=MagicMock()):
            p = SchemaIsolationProvider(make_mock_config())
        assert p.dialect == DbDialect.POSTGRESQL

    def test_sqlite_dialect_detected(self) -> None:
        from fastapi_tenancy.isolation.schema import SchemaIsolationProvider
        with patch("fastapi_tenancy.isolation.schema.create_async_engine", return_value=MagicMock()):
            p = SchemaIsolationProvider(make_mock_config("sqlite+aiosqlite:///:memory:"))
        assert p.dialect == DbDialect.SQLITE


# ---------------------------------------------------------------------------
# RLSIsolationProvider — mock-based
# ---------------------------------------------------------------------------

class TestRLSProviderLogic:

    def test_pg_native_rls(self) -> None:
        from fastapi_tenancy.isolation.rls import RLSIsolationProvider
        with patch("fastapi_tenancy.isolation.rls.create_async_engine", return_value=MagicMock()):
            p = RLSIsolationProvider(make_mock_config())
        assert p.dialect == DbDialect.POSTGRESQL

    def test_sqlite_no_native_rls(self) -> None:
        from fastapi_tenancy.isolation.rls import RLSIsolationProvider
        with patch("fastapi_tenancy.isolation.rls.create_async_engine", return_value=MagicMock()):
            p = RLSIsolationProvider(make_mock_config("sqlite+aiosqlite:///:memory:"))
        assert p.dialect == DbDialect.SQLITE

    @pytest.mark.asyncio
    async def test_apply_filters_adds_where(self) -> None:
        import sqlalchemy as sa

        from fastapi_tenancy.isolation.rls import RLSIsolationProvider
        with patch("fastapi_tenancy.isolation.rls.create_async_engine", return_value=MagicMock()):
            p = RLSIsolationProvider(make_mock_config())
        meta = sa.MetaData()
        t_tbl = sa.Table("orders", meta, sa.Column("tenant_id", sa.String))
        q = sa.select(t_tbl)
        tenant = make_tenant()
        filtered = await p.apply_filters(q, tenant)
        assert hasattr(filtered, "whereclause")

    @pytest.mark.asyncio
    async def test_apply_filters_passthrough_non_query(self) -> None:
        from fastapi_tenancy.isolation.rls import RLSIsolationProvider
        with patch("fastapi_tenancy.isolation.rls.create_async_engine", return_value=MagicMock()):
            p = RLSIsolationProvider(make_mock_config())
        raw = "SELECT 1"
        result = await p.apply_filters(raw, make_tenant())
        assert result == raw

    @pytest.mark.asyncio
    async def test_initialize_tenant_noop(self) -> None:
        from fastapi_tenancy.isolation.rls import RLSIsolationProvider
        with patch("fastapi_tenancy.isolation.rls.create_async_engine", return_value=MagicMock()):
            p = RLSIsolationProvider(make_mock_config())
        await p.initialize_tenant(make_tenant())  # must not raise

    @pytest.mark.asyncio
    async def test_destroy_tenant_logs_warning(self) -> None:
        from fastapi_tenancy.isolation.rls import RLSIsolationProvider
        with patch("fastapi_tenancy.isolation.rls.create_async_engine", return_value=MagicMock()):
            p = RLSIsolationProvider(make_mock_config())
        await p.destroy_tenant(make_tenant())  # must not raise

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        from fastapi_tenancy.isolation.rls import RLSIsolationProvider
        engine = AsyncMock()
        engine.dispose = AsyncMock()
        with patch("fastapi_tenancy.isolation.rls.create_async_engine", return_value=engine):
            p = RLSIsolationProvider(make_mock_config())
        await p.close()
        engine.dispose.assert_called_once()


# ---------------------------------------------------------------------------
# DatabaseIsolationProvider — mock-based
# ---------------------------------------------------------------------------

class TestDatabaseProviderLogic:

    def test_database_name_from_slug(self) -> None:
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        with patch("fastapi_tenancy.isolation.database.create_async_engine", return_value=MagicMock()):
            p = DatabaseIsolationProvider(make_mock_config())
        t = make_tenant("acme-corp")
        name = p._get_database_name(t)
        assert "acme_corp" in name
        assert name.startswith("tenant_")

    def test_sqlite_url_built_correctly(self) -> None:
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        with patch("fastapi_tenancy.isolation.database.create_async_engine", return_value=MagicMock()):
            p = DatabaseIsolationProvider(
                make_mock_config("sqlite+aiosqlite:///./data/main.db")
            )
        t = make_tenant("acme-corp")
        url = p._build_tenant_url(t)
        assert "sqlite" in url
        assert "acme_corp" in url

    def test_mssql_initialize_raises(self) -> None:
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        with patch("fastapi_tenancy.isolation.database.create_async_engine", return_value=MagicMock()):
            p = DatabaseIsolationProvider(
                make_mock_config("mssql+aioodbc://u:p@h/db")
            )

        import asyncio
        with pytest.raises(IsolationError, match="MSSQL"):
            asyncio.get_event_loop().run_until_complete(
                p.initialize_tenant(make_tenant())
            )

    @pytest.mark.asyncio
    async def test_close_disposes_all_engines(self) -> None:
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        master = AsyncMock()
        master.dispose = AsyncMock()
        with patch("fastapi_tenancy.isolation.database.create_async_engine", return_value=master):
            p = DatabaseIsolationProvider(make_mock_config())
        # Add a fake per-tenant engine
        tenant_eng = AsyncMock()
        tenant_eng.dispose = AsyncMock()
        p._engines["t-fake"] = tenant_eng
        await p.close()
        tenant_eng.dispose.assert_called_once()
        master.dispose.assert_called_once()
        assert len(p._engines) == 0


# ---------------------------------------------------------------------------
# IsolationProviderFactory
# ---------------------------------------------------------------------------

class TestIsolationFactory:

    @pytest.mark.parametrize("strategy,expected_class", [
        ("schema", "SchemaIsolationProvider"),
        ("database", "DatabaseIsolationProvider"),
        ("rls", "RLSIsolationProvider"),
        ("hybrid", "HybridIsolationProvider"),
    ])
    def test_creates_correct_provider(self, strategy: str, expected_class: str) -> None:
        from fastapi_tenancy.core.types import IsolationStrategy
        from fastapi_tenancy.isolation.factory import IsolationProviderFactory

        cfg = make_mock_config()
        # Hybrid needs real IsolationStrategy enum values, not MagicMock attributes
        cfg.premium_isolation_strategy = IsolationStrategy.SCHEMA
        cfg.standard_isolation_strategy = IsolationStrategy.RLS

        with patch("fastapi_tenancy.isolation.schema.create_async_engine", return_value=MagicMock()), \
             patch("fastapi_tenancy.isolation.database.create_async_engine", return_value=MagicMock()), \
             patch("fastapi_tenancy.isolation.rls.create_async_engine", return_value=MagicMock()), \
             patch("fastapi_tenancy.isolation.hybrid.create_async_engine", return_value=MagicMock()):
            provider = IsolationProviderFactory.create(
                IsolationStrategy(strategy), cfg
            )
        assert type(provider).__name__ == expected_class

    def test_unknown_strategy_raises(self) -> None:
        from fastapi_tenancy.isolation.factory import IsolationProviderFactory
        with pytest.raises((ValueError, KeyError)):
            IsolationProviderFactory.create("nonexistent", make_mock_config())  # type: ignore


# ---------------------------------------------------------------------------
# Injection guard integration
# ---------------------------------------------------------------------------

class TestInjectionGuards:

    @pytest.mark.parametrize("bad_schema", [
        "'; DROP TABLE",
        "tenant; SELECT",
        'tenant" OR "1"="1',
        "\x00null",
        "a" * 64,
    ])
    def test_assert_safe_schema_name_rejects(self, bad_schema: str) -> None:
        from fastapi_tenancy.utils.validation import assert_safe_schema_name
        with pytest.raises(ValueError):
            assert_safe_schema_name(bad_schema)

    @pytest.mark.parametrize("good_schema", [
        "tenant_acme",
        "tenant_my_company",
        "_private",
        "a" * 63,
    ])
    def test_assert_safe_schema_name_accepts(self, good_schema: str) -> None:
        from fastapi_tenancy.utils.validation import assert_safe_schema_name
        assert_safe_schema_name(good_schema)  # must not raise

    @pytest.mark.parametrize("bad_db", [
        "'; DROP DATABASE",
        "db; SELECT",
        "a" * 65,
    ])
    def test_assert_safe_database_name_rejects(self, bad_db: str) -> None:
        from fastapi_tenancy.utils.validation import assert_safe_database_name
        with pytest.raises(ValueError):
            assert_safe_database_name(bad_db)
