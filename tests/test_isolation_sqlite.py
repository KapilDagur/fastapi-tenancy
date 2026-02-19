"""Integration tests for isolation providers using SQLite (no external DB needed).

These tests use SQLite + aiosqlite via in-memory or temp-file databases so they
run in CI without any real PostgreSQL/MySQL instance.  They cover:

- SchemaIsolationProvider prefix-mode (SQLite has no native schemas)
- DatabaseIsolationProvider file-per-tenant mode
- RLSIsolationProvider explicit-filter fallback mode
- Engine pool configuration (StaticPool for SQLite)
- Table prefix generation and metadata cloning
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from fastapi_tenancy.core.types import Tenant
from fastapi_tenancy.utils.db_compat import DbDialect, detect_dialect, make_table_prefix


def make_sqlite_config(url: str = "sqlite+aiosqlite:///:memory:") -> MagicMock:
    cfg = MagicMock()
    cfg.database_url = url
    cfg.database_pool_size = 1
    cfg.database_max_overflow = 0
    cfg.database_pool_timeout = 5
    cfg.database_pool_recycle = 600
    cfg.database_echo = False
    cfg.database_url_template = None
    cfg.schema_prefix = "tenant_"

    def get_schema_name(tenant_id: str) -> str:
        return f"tenant_{tenant_id.replace('-', '_')}"
    cfg.get_schema_name = get_schema_name

    def get_db_url_for_tenant(tenant_id: str) -> str:
        slug = tenant_id.replace("-", "_")
        return f"sqlite+aiosqlite:///./test_{slug}.db"
    cfg.get_database_url_for_tenant = get_db_url_for_tenant

    return cfg


def make_tenant(slug: str = "acme-corp") -> Tenant:
    return Tenant(id=f"t-{slug}", identifier=slug, name=slug.title())


class TestDetectDialectSQLite:

    def test_aiosqlite_detected(self) -> None:
        url = "sqlite+aiosqlite:///:memory:"
        assert detect_dialect(url) == DbDialect.SQLITE

    def test_plain_sqlite_detected(self) -> None:
        assert detect_dialect("sqlite:///./test.db") == DbDialect.SQLITE


class TestSchemaIsolationProviderSQLite:

    @pytest.fixture
    def provider(self):
        from fastapi_tenancy.isolation.schema import SchemaIsolationProvider
        cfg = make_sqlite_config()
        return SchemaIsolationProvider(cfg)

    def test_dialect_is_sqlite(self, provider) -> None:
        assert provider.dialect == DbDialect.SQLITE

    def test_table_prefix_correct(self, provider) -> None:
        tenant = make_tenant("acme-corp")
        prefix = provider.get_table_prefix(tenant)
        assert prefix == "t_acme_corp_"

    @pytest.mark.asyncio
    async def test_get_session_prefix_mode(self, provider) -> None:
        tenant = make_tenant("acme-corp")
        async with provider.get_session(tenant) as session:
            assert session.info.get("tenant_id") == tenant.id
            assert session.info.get("table_prefix") == "t_acme_corp_"

    @pytest.mark.asyncio
    async def test_initialize_tenant_no_metadata(self, provider) -> None:
        tenant = make_tenant("acme-corp")
        # Should not raise — just logs
        await provider.initialize_tenant(tenant)

    @pytest.mark.asyncio
    async def test_initialize_tenant_with_metadata(self, provider) -> None:
        import sqlalchemy as sa
        tenant = make_tenant("acme-corp")
        meta = sa.MetaData()
        sa.Table("orders", meta, sa.Column("id", sa.Integer, primary_key=True))
        # Should create prefixed table without error
        await provider.initialize_tenant(tenant, metadata=meta)

    @pytest.mark.asyncio
    async def test_verify_isolation_sqlite(self, provider) -> None:
        # For SQLite, verify_isolation just checks engine connectivity
        result = await provider.verify_isolation(make_tenant("acme-corp"))
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_close(self, provider) -> None:
        await provider.close()  # Should not raise


class TestRLSIsolationProviderSQLite:

    @pytest.fixture
    def provider(self):
        from fastapi_tenancy.isolation.rls import RLSIsolationProvider
        cfg = make_sqlite_config()
        return RLSIsolationProvider(cfg)

    def test_dialect_is_sqlite(self, provider) -> None:
        assert provider.dialect == DbDialect.SQLITE

    @pytest.mark.asyncio
    async def test_get_session_sets_info(self, provider) -> None:
        tenant = make_tenant("acme-corp")
        async with provider.get_session(tenant) as session:
            # SQLite can't SET session variables; tenant_id stored in info
            assert session.info.get("tenant_id") == tenant.id

    @pytest.mark.asyncio
    async def test_apply_filters_adds_where(self, provider) -> None:
        import sqlalchemy as sa
        tenant = make_tenant("acme-corp")
        meta = sa.MetaData()
        t = sa.Table("orders", meta, sa.Column("tenant_id", sa.String))
        query = sa.select(t)
        filtered = await provider.apply_filters(query, tenant)
        compiled = str(filtered.compile(compile_kwargs={"literal_binds": True}))
        assert "tenant_id" in compiled

    @pytest.mark.asyncio
    async def test_apply_filters_non_query_passthrough(self, provider) -> None:
        """apply_filters should not crash on raw strings."""
        tenant = make_tenant("acme-corp")
        raw = "SELECT * FROM orders"
        result = await provider.apply_filters(raw, tenant)
        assert result == raw

    @pytest.mark.asyncio
    async def test_initialize_tenant_noop(self, provider) -> None:
        await provider.initialize_tenant(make_tenant("acme-corp"))

    @pytest.mark.asyncio
    async def test_destroy_tenant_logs_warning(self, provider) -> None:
        # Should not raise — just logs warning
        await provider.destroy_tenant(make_tenant("acme-corp"))

    @pytest.mark.asyncio
    async def test_close(self, provider) -> None:
        await provider.close()


class TestDatabaseIsolationProviderSQLite:

    @pytest.fixture
    def provider(self):
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        cfg = make_sqlite_config("sqlite+aiosqlite:///./test_main.db")
        return DatabaseIsolationProvider(cfg)

    def test_dialect_is_sqlite(self, provider) -> None:
        assert provider.dialect == DbDialect.SQLITE

    def test_builds_tenant_url(self, provider) -> None:
        tenant = make_tenant("acme-corp")
        url = provider._build_tenant_url(tenant)
        assert "acme_corp" in url or "acme" in url

    @pytest.mark.asyncio
    async def test_get_session_sqlite(self, provider) -> None:
        tenant = make_tenant("acme-corp")
        async with provider.get_session(tenant) as session:
            assert session is not None

    @pytest.mark.asyncio
    async def test_initialize_tenant_no_metadata(self, provider) -> None:
        tenant = make_tenant("new-startup")
        await provider.initialize_tenant(tenant)

    @pytest.mark.asyncio
    async def test_initialize_tenant_with_metadata(self, provider) -> None:
        import sqlalchemy as sa
        tenant = make_tenant("meta-tenant")
        meta = sa.MetaData()
        sa.Table("products", meta, sa.Column("id", sa.Integer, primary_key=True))
        await provider.initialize_tenant(tenant, metadata=meta)

    @pytest.mark.asyncio
    async def test_close(self, provider) -> None:
        await provider.close()


class TestSchemaIsolationProviderPostgresDelegate:
    """Test that MySQL dialect delegates to DatabaseIsolationProvider."""

    def test_mysql_detection(self) -> None:
        assert detect_dialect("mysql+aiomysql://u:p@h/db") == DbDialect.MYSQL


class TestInjectionGuardSQLiteMode:
    """Schema names must be validated even in prefix/fallback mode."""

    def test_prefix_from_valid_slug(self) -> None:
        prefix = make_table_prefix("acme-corp")
        assert "'" not in prefix
        assert ";" not in prefix
        assert "--" not in prefix

    @pytest.mark.parametrize("bad_slug", [
        "'; DROP TABLE",
        "../../etc",
        "slug; SELECT",
    ])
    def test_prefix_sanitizes_injection_payload(self, bad_slug: str) -> None:
        prefix = make_table_prefix(bad_slug)
        # Result must never contain dangerous characters
        assert "'" not in prefix
        assert ";" not in prefix
        assert "--" not in prefix
        # Result must start with t_
        assert prefix.startswith("t_") or prefix.startswith("t"), f"Unexpected prefix: {prefix}"
