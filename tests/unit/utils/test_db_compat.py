"""Unit tests for fastapi_tenancy.utils.db_compat"""

from __future__ import annotations

import pytest

from fastapi_tenancy.utils.db_compat import (
    DbDialect,
    _sanitize_identifier,
    detect_dialect,
    get_schema_set_sql,
    get_set_tenant_sql,
    make_table_prefix,
    requires_static_pool,
    supports_native_rls,
    supports_native_schemas,
)
from fastapi_tenancy.utils.validation import sanitize_identifier


class TestDetectDialect:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("postgresql+asyncpg://user:pass@host/db", DbDialect.POSTGRESQL),
            ("postgresql://user:pass@host/db", DbDialect.POSTGRESQL),
            ("postgresql+psycopg://user:pass@host/db", DbDialect.POSTGRESQL),
            ("asyncpg://user:pass@host/db", DbDialect.POSTGRESQL),
            ("sqlite+aiosqlite:///./test.db", DbDialect.SQLITE),
            ("sqlite:///./test.db", DbDialect.SQLITE),
            ("aiosqlite:///test.db", DbDialect.SQLITE),
            ("mysql+aiomysql://user:pass@host/db", DbDialect.MYSQL),
            ("mysql://user:pass@host/db", DbDialect.MYSQL),
            ("mariadb+aiomysql://user:pass@host/db", DbDialect.MYSQL),
            ("mssql+aioodbc://user:pass@host/db", DbDialect.MSSQL),
            ("mssql://user:pass@host/db", DbDialect.MSSQL),
            ("oracle://user:pass@host/db", DbDialect.UNKNOWN),
            ("not-a-url", DbDialect.UNKNOWN),
        ],
    )
    def test_detect(self, url, expected):
        assert detect_dialect(url) == expected


class TestCapabilityPredicates:
    def test_supports_native_schemas_postgres(self):
        assert supports_native_schemas(DbDialect.POSTGRESQL) is True

    def test_supports_native_schemas_mssql(self):
        assert supports_native_schemas(DbDialect.MSSQL) is True

    def test_no_native_schemas_sqlite(self):
        assert supports_native_schemas(DbDialect.SQLITE) is False

    def test_no_native_schemas_mysql(self):
        assert supports_native_schemas(DbDialect.MYSQL) is False

    def test_native_rls_only_postgres(self):
        assert supports_native_rls(DbDialect.POSTGRESQL) is True
        assert supports_native_rls(DbDialect.MYSQL) is False
        assert supports_native_rls(DbDialect.SQLITE) is False
        assert supports_native_rls(DbDialect.MSSQL) is False

    def test_static_pool_only_sqlite(self):
        assert requires_static_pool(DbDialect.SQLITE) is True
        assert requires_static_pool(DbDialect.POSTGRESQL) is False


class TestGetSql:
    def test_set_tenant_sql_postgres(self):
        sql = get_set_tenant_sql(DbDialect.POSTGRESQL)
        assert sql is not None
        assert "tenant_id" in sql

    def test_set_tenant_sql_non_postgres_returns_none(self):
        assert get_set_tenant_sql(DbDialect.SQLITE) is None
        assert get_set_tenant_sql(DbDialect.MYSQL) is None

    def test_schema_set_sql_postgres(self):
        sql = get_schema_set_sql(DbDialect.POSTGRESQL)
        assert sql is not None
        assert "search_path" in sql

    def test_schema_set_sql_non_postgres_returns_none(self):
        assert get_schema_set_sql(DbDialect.SQLITE) is None


class TestMakeTablePrefix:
    def test_basic(self):
        prefix = make_table_prefix("acme-corp")
        assert prefix.startswith("t_")
        assert prefix.endswith("_")

    def test_long_slug_truncated(self):
        prefix = make_table_prefix("a" * 50)
        # base portion capped at 20 chars
        assert len(prefix) <= 25

    def test_my_company(self):
        prefix = make_table_prefix("my.company")
        assert "company" in prefix or "my" in prefix


class TestInternalSanitizeIdentifier:
    def test_same_as_public(self):
        """db_compat._sanitize_identifier must match utils.validation.sanitize_identifier."""
        test_cases = [
            "acme-corp",
            "my.company",
            "2fast",
            "A B C",
            "!!!",
            "",
            "a" * 100,
        ]
        for case in test_cases:
            assert _sanitize_identifier(case) == sanitize_identifier(case), (
                f"Mismatch for {case!r}: "
                f"_sanitize_identifier={_sanitize_identifier(case)!r} "
                f"sanitize_identifier={sanitize_identifier(case)!r}"
            )
