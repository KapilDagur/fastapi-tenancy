"""Unit tests for TenancyConfig — validation, helpers, multi-db support."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from fastapi_tenancy.core.config import TenancyConfig
from fastapi_tenancy.core.types import IsolationStrategy, ResolutionStrategy


def make_config(**kwargs) -> TenancyConfig:
    defaults = dict(
        database_url="postgresql+asyncpg://user:pass@localhost/mydb",
        resolution_strategy="header",
        isolation_strategy="rls",
    )
    defaults.update(kwargs)
    return TenancyConfig(**defaults)


class TestBasicConfig:

    def test_minimal_pg_config(self) -> None:
        cfg = make_config()
        assert cfg.isolation_strategy == IsolationStrategy.RLS
        assert cfg.resolution_strategy == ResolutionStrategy.HEADER

    def test_sqlite_url_accepted(self) -> None:
        """SQLite must no longer be rejected."""
        cfg = TenancyConfig(
            database_url="sqlite+aiosqlite:///./test.db",
            resolution_strategy="header",
            isolation_strategy="rls",
        )
        assert "sqlite" in str(cfg.database_url)

    def test_mysql_url_accepted(self) -> None:
        cfg = TenancyConfig(
            database_url="mysql+aiomysql://user:pass@localhost/mydb",
            resolution_strategy="header",
            isolation_strategy="rls",
        )
        assert "mysql" in str(cfg.database_url)

    def test_str_masks_password(self) -> None:
        cfg = make_config()
        s = str(cfg)
        assert "pass" not in s or "***" in s


class TestGetSchemaName:

    def test_valid_slug(self) -> None:
        cfg = make_config()
        name = cfg.get_schema_name("acme-corp")
        assert name == "tenant_acme_corp"

    def test_prefix_applied(self) -> None:
        cfg = make_config(schema_prefix="app_")
        name = cfg.get_schema_name("widgets-inc")
        assert name.startswith("app_")

    def test_invalid_slug_raises(self) -> None:
        cfg = make_config()
        with pytest.raises(ValueError):
            cfg.get_schema_name("INVALID-UPPER")

    def test_injection_slug_raises(self) -> None:
        cfg = make_config()
        with pytest.raises(ValueError):
            cfg.get_schema_name("'; DROP TABLE")


class TestIsPremiumTenant:

    def test_premium_detected(self) -> None:
        cfg = make_config(
            isolation_strategy="hybrid",
            premium_tenants=["acme-corp", "widgets-inc"],
        )
        assert cfg.is_premium_tenant("acme-corp") is True

    def test_standard_not_premium(self) -> None:
        cfg = make_config(
            isolation_strategy="hybrid",
            premium_tenants=["acme-corp"],
        )
        assert cfg.is_premium_tenant("standard-co") is False


class TestJwtValidation:

    def test_jwt_secret_required_for_jwt_strategy(self) -> None:
        with pytest.raises(ValidationError, match="jwt_secret"):
            TenancyConfig(
                database_url="postgresql+asyncpg://u:p@h/db",
                resolution_strategy="jwt",
                isolation_strategy="rls",
                # jwt_secret missing
            )

    def test_jwt_secret_too_short(self) -> None:
        with pytest.raises(ValidationError):
            make_config(
                resolution_strategy="jwt",
                jwt_secret="short",
            )

    def test_jwt_secret_accepted(self) -> None:
        cfg = make_config(
            resolution_strategy="jwt",
            jwt_secret="a" * 32,
        )
        assert cfg.jwt_secret is not None


class TestSubdomainValidation:

    def test_domain_suffix_required_for_subdomain(self) -> None:
        with pytest.raises(ValidationError, match="domain_suffix"):
            make_config(resolution_strategy="subdomain")

    def test_domain_suffix_accepted(self) -> None:
        cfg = make_config(
            resolution_strategy="subdomain",
            domain_suffix=".example.com",
        )
        assert cfg.domain_suffix == ".example.com"


class TestSchemaPrefixValidation:

    def test_valid_prefix(self) -> None:
        cfg = make_config(schema_prefix="myapp_")
        assert cfg.schema_prefix == "myapp_"

    def test_invalid_prefix_uppercase(self) -> None:
        with pytest.raises(ValidationError):
            make_config(schema_prefix="MyApp_")

    def test_invalid_prefix_starts_with_digit(self) -> None:
        with pytest.raises(ValidationError):
            make_config(schema_prefix="1app_")


class TestGetDatabaseUrlForTenant:

    def test_template_applied(self) -> None:
        cfg = make_config(
            database_url_template="postgresql+asyncpg://u:p@h/{database_name}"
        )
        url = cfg.get_database_url_for_tenant("acme-corp")
        assert "tenant_acme_corp_db" in url

    def test_fallback_to_base_url(self) -> None:
        cfg = make_config()
        url = cfg.get_database_url_for_tenant("acme-corp")
        assert "mydb" in url or "localhost" in url
