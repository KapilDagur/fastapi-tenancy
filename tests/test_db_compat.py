"""Unit tests for db_compat — dialect detection and capability matrix."""
from __future__ import annotations

import pytest

from fastapi_tenancy.utils.db_compat import (
    DbDialect,
    detect_dialect,
    get_schema_set_sql,
    get_set_tenant_sql,
    make_table_prefix,
    requires_static_pool,
    supports_native_rls,
    supports_native_schemas,
)


class TestDetectDialect:

    @pytest.mark.parametrize("url,expected", [
        ("postgresql+asyncpg://user:pass@localhost/db", DbDialect.POSTGRESQL),
        ("postgresql://user:pass@localhost/db", DbDialect.POSTGRESQL),
        ("postgresql+psycopg://user:pass@localhost/db", DbDialect.POSTGRESQL),
        ("postgresql+psycopg2://user:pass@localhost/db", DbDialect.POSTGRESQL),
        ("sqlite+aiosqlite:///./test.db", DbDialect.SQLITE),
        ("sqlite:///./test.db", DbDialect.SQLITE),
        ("mysql+aiomysql://user:pass@localhost/db", DbDialect.MYSQL),
        ("mysql://user:pass@localhost/db", DbDialect.MYSQL),
        ("mariadb+aiomysql://user:pass@localhost/db", DbDialect.MYSQL),
        ("mssql+aioodbc://user:pass@localhost/db", DbDialect.MSSQL),
        ("oracle+cx_oracle://user:pass@localhost/db", DbDialect.UNKNOWN),
        ("not-a-url", DbDialect.UNKNOWN),
    ])
    def test_detection(self, url: str, expected: DbDialect) -> None:
        assert detect_dialect(url) == expected


class TestNativeCapabilities:

    def test_pg_supports_schemas(self) -> None:
        assert supports_native_schemas(DbDialect.POSTGRESQL) is True

    def test_sqlite_no_schemas(self) -> None:
        assert supports_native_schemas(DbDialect.SQLITE) is False

    def test_mysql_no_schemas(self) -> None:
        assert supports_native_schemas(DbDialect.MYSQL) is False

    def test_pg_supports_rls(self) -> None:
        assert supports_native_rls(DbDialect.POSTGRESQL) is True

    def test_sqlite_no_rls(self) -> None:
        assert supports_native_rls(DbDialect.SQLITE) is False

    def test_mysql_no_rls(self) -> None:
        assert supports_native_rls(DbDialect.MYSQL) is False


class TestRequiresStaticPool:

    def test_sqlite_needs_static_pool(self) -> None:
        assert requires_static_pool(DbDialect.SQLITE) is True

    def test_pg_no_static_pool(self) -> None:
        assert requires_static_pool(DbDialect.POSTGRESQL) is False

    def test_mysql_no_static_pool(self) -> None:
        assert requires_static_pool(DbDialect.MYSQL) is False


class TestSetTenantSql:

    def test_pg_returns_set_statement(self) -> None:
        sql = get_set_tenant_sql(DbDialect.POSTGRESQL, "t1")
        assert sql is not None
        assert "app.current_tenant" in sql

    def test_mysql_returns_user_var(self) -> None:
        sql = get_set_tenant_sql(DbDialect.MYSQL, "t1")
        assert sql is not None
        assert "@current_tenant" in sql

    def test_sqlite_returns_none(self) -> None:
        assert get_set_tenant_sql(DbDialect.SQLITE, "t1") is None

    def test_unknown_returns_none(self) -> None:
        assert get_set_tenant_sql(DbDialect.UNKNOWN, "t1") is None


class TestSchemaSetSql:

    def test_pg_returns_search_path(self) -> None:
        sql = get_schema_set_sql(DbDialect.POSTGRESQL, "tenant_x")
        assert sql is not None
        assert "search_path" in sql

    def test_sqlite_returns_none(self) -> None:
        assert get_schema_set_sql(DbDialect.SQLITE, "tenant_x") is None


class TestMakeTablePrefix:

    @pytest.mark.parametrize("slug,expected_start", [
        ("acme-corp", "t_acme_corp_"),
        ("my.company", "t_my_company_"),
        ("UPPER", "t_upper_"),
        ("tenant-01", "t_tenant_01_"),
    ])
    def test_prefix_format(self, slug: str, expected_start: str) -> None:
        prefix = make_table_prefix(slug)
        assert prefix == expected_start, f"Expected {expected_start!r}, got {prefix!r}"

    def test_prefix_max_length(self) -> None:
        long_slug = "a" * 50
        prefix = make_table_prefix(long_slug)
        # Prefix must be short enough that table names can be appended
        assert len(prefix) <= 25

    @pytest.mark.parametrize("slug", [
        "acme-corp", "my-tenant", "tenant-01",
    ])
    def test_prefix_has_trailing_underscore(self, slug: str) -> None:
        prefix = make_table_prefix(slug)
        assert prefix.endswith("_")
