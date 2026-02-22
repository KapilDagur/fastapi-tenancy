"""Unit tests for fastapi_tenancy.core.config"""

from __future__ import annotations

import warnings

from pydantic import ValidationError
import pytest

from fastapi_tenancy.core.config import TenancyConfig
from fastapi_tenancy.core.types import IsolationStrategy, ResolutionStrategy

BASE_URL = "postgresql+asyncpg://user:pass@localhost/db"
SQLITE_URL = "sqlite+aiosqlite:///./test.db"


def _cfg(**kwargs) -> TenancyConfig:
    return TenancyConfig(database_url=BASE_URL, **kwargs)


class TestBasicConstruction:
    def test_minimal(self):
        cfg = _cfg()
        assert cfg.database_url == BASE_URL
        assert cfg.resolution_strategy == ResolutionStrategy.HEADER
        assert cfg.isolation_strategy == IsolationStrategy.SCHEMA

    def test_all_defaults(self):
        cfg = _cfg()
        assert cfg.database_pool_size == 20
        assert cfg.database_max_overflow == 40
        assert cfg.database_pool_timeout == 30
        assert cfg.database_pool_recycle == 3600
        assert cfg.database_pool_pre_ping is True
        assert cfg.database_echo is False
        assert cfg.cache_ttl == 3600
        assert cfg.cache_enabled is False
        assert cfg.enable_rate_limiting is False
        assert cfg.rate_limit_per_minute == 100
        assert cfg.rate_limit_window_seconds == 60
        assert cfg.tenant_header_name == "X-Tenant-ID"
        assert cfg.allow_tenant_registration is False
        assert cfg.max_tenants is None
        assert cfg.enable_soft_delete is True
        assert cfg.enable_audit_logging is True
        assert cfg.enable_encryption is False
        assert cfg.schema_prefix == "tenant_"
        assert cfg.public_schema == "public"
        assert cfg.premium_tenants == []


class TestDatabaseUrlValidator:
    def test_strips_trailing_slash(self):
        cfg = TenancyConfig(database_url=BASE_URL + "/")
        assert not cfg.database_url.endswith("/")

    def test_warns_on_sync_driver_postgres(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            TenancyConfig(database_url="postgresql://user:pass@localhost/db")
            assert any(issubclass(warning.category, UserWarning) for warning in w)

    def test_warns_on_sync_driver_sqlite(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            TenancyConfig(database_url="sqlite:///./test.db")
            assert any(issubclass(warning.category, UserWarning) for warning in w)

    def test_no_warn_for_async_driver(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            TenancyConfig(database_url=SQLITE_URL)
            user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
            assert len(user_warnings) == 0


class TestJwtSecretValidator:
    def test_requires_jwt_secret_for_jwt_strategy(self):
        with pytest.raises(ValidationError):
            _cfg(resolution_strategy=ResolutionStrategy.JWT)

    def test_short_jwt_secret_rejected(self):
        with pytest.raises(ValidationError):
            _cfg(
                resolution_strategy=ResolutionStrategy.JWT,
                jwt_secret="tooshort",
            )

    def test_valid_jwt_secret(self):
        cfg = _cfg(
            resolution_strategy=ResolutionStrategy.JWT,
            jwt_secret="a" * 32,
        )
        assert cfg.jwt_secret == "a" * 32

    def test_long_jwt_secret(self):
        cfg = _cfg(jwt_secret="x" * 64)
        assert cfg.jwt_secret is not None

    def test_jwt_secret_optional_for_non_jwt(self):
        cfg = _cfg()
        assert cfg.jwt_secret is None


class TestDomainSuffixValidator:
    def test_requires_suffix_for_subdomain(self):
        with pytest.raises(ValidationError):
            _cfg(resolution_strategy=ResolutionStrategy.SUBDOMAIN)

    def test_valid_domain_suffix(self):
        cfg = _cfg(
            resolution_strategy=ResolutionStrategy.SUBDOMAIN,
            domain_suffix=".example.com",
        )
        assert cfg.domain_suffix == ".example.com"

    def test_no_suffix_required_for_non_subdomain(self):
        cfg = _cfg()
        assert cfg.domain_suffix is None


class TestEncryptionKeyValidator:
    def test_requires_key_when_encryption_enabled(self):
        with pytest.raises(ValidationError):
            _cfg(enable_encryption=True)

    def test_short_encryption_key_rejected(self):
        with pytest.raises(ValidationError):
            _cfg(enable_encryption=True, encryption_key="tooshort")

    def test_valid_encryption_key(self):
        cfg = _cfg(enable_encryption=True, encryption_key="a" * 32)
        assert cfg.enable_encryption is True

    def test_no_key_required_when_disabled(self):
        cfg = _cfg()
        assert cfg.encryption_key is None


class TestSchemaPrefixValidator:
    def test_invalid_prefix(self):
        with pytest.raises(ValidationError):
            _cfg(schema_prefix="1invalid")

    def test_uppercase_prefix(self):
        with pytest.raises(ValidationError):
            _cfg(schema_prefix="Tenant_")

    def test_valid_prefix(self):
        cfg = _cfg(schema_prefix="t_")
        assert cfg.schema_prefix == "t_"

    def test_hyphen_in_prefix(self):
        with pytest.raises(ValidationError):
            _cfg(schema_prefix="my-tenant")


class TestCrossFieldValidation:
    def test_cache_enabled_requires_redis_url(self):
        with pytest.raises(ValidationError):
            _cfg(cache_enabled=True)

    def test_cache_enabled_with_redis_url(self):
        cfg = _cfg(cache_enabled=True, redis_url="redis://localhost:6379/0")
        assert cfg.cache_enabled is True

    def test_rate_limiting_requires_redis_url(self):
        with pytest.raises(ValidationError):
            _cfg(enable_rate_limiting=True)

    def test_rate_limiting_with_redis_url(self):
        cfg = _cfg(enable_rate_limiting=True, redis_url="redis://localhost:6379/0")
        assert cfg.enable_rate_limiting is True

    def test_hybrid_requires_distinct_strategies(self):
        with pytest.raises(ValidationError):
            _cfg(
                isolation_strategy=IsolationStrategy.HYBRID,
                premium_isolation_strategy=IsolationStrategy.RLS,
                standard_isolation_strategy=IsolationStrategy.RLS,
            )

    def test_hybrid_with_distinct_strategies(self):
        cfg = _cfg(
            isolation_strategy=IsolationStrategy.HYBRID,
            premium_isolation_strategy=IsolationStrategy.SCHEMA,
            standard_isolation_strategy=IsolationStrategy.RLS,
        )
        assert cfg.isolation_strategy == IsolationStrategy.HYBRID

    def test_database_isolation_requires_url_template(self):
        with pytest.raises(ValidationError):
            _cfg(isolation_strategy=IsolationStrategy.DATABASE)

    def test_database_isolation_with_template(self):
        cfg = _cfg(
            isolation_strategy=IsolationStrategy.DATABASE,
            database_url_template="postgresql+asyncpg://user:pass@host/{database_name}",
        )
        assert cfg.isolation_strategy == IsolationStrategy.DATABASE


class TestHelperMethods:
    def test_get_schema_name(self):
        cfg = _cfg(schema_prefix="tenant_")
        assert cfg.get_schema_name("acme-corp") == "tenant_acme_corp"

    def test_get_schema_name_with_dots(self):
        cfg = _cfg(schema_prefix="t_")
        assert cfg.get_schema_name("my-corp") == "t_my_corp"

    def test_get_schema_name_invalid_identifier(self):
        cfg = _cfg()
        with pytest.raises(ValueError):
            cfg.get_schema_name("INVALID")

    def test_get_database_url_for_tenant_with_template(self):
        cfg = _cfg(
            isolation_strategy=IsolationStrategy.DATABASE,
            database_url_template="postgresql+asyncpg://u:p@host/{database_name}",
        )
        url = cfg.get_database_url_for_tenant("acme-corp")
        assert "tenant_acme_corp_db" in url

    def test_get_database_url_no_template(self):
        cfg = _cfg()
        url = cfg.get_database_url_for_tenant("acme-corp")
        assert url == BASE_URL

    def test_is_premium_tenant_true(self):
        cfg = _cfg(premium_tenants=["tid1", "tid2"])
        assert cfg.is_premium_tenant("tid1") is True

    def test_is_premium_tenant_false(self):
        cfg = _cfg(premium_tenants=["tid1"])
        assert cfg.is_premium_tenant("other") is False

    def test_get_isolation_strategy_non_hybrid(self):
        cfg = _cfg(isolation_strategy=IsolationStrategy.RLS)
        assert cfg.get_isolation_strategy_for_tenant("any") == IsolationStrategy.RLS

    def test_get_isolation_strategy_hybrid_premium(self):
        cfg = _cfg(
            isolation_strategy=IsolationStrategy.HYBRID,
            premium_isolation_strategy=IsolationStrategy.SCHEMA,
            standard_isolation_strategy=IsolationStrategy.RLS,
            premium_tenants=["tid1"],
        )
        assert cfg.get_isolation_strategy_for_tenant("tid1") == IsolationStrategy.SCHEMA

    def test_get_isolation_strategy_hybrid_standard(self):
        cfg = _cfg(
            isolation_strategy=IsolationStrategy.HYBRID,
            premium_isolation_strategy=IsolationStrategy.SCHEMA,
            standard_isolation_strategy=IsolationStrategy.RLS,
            premium_tenants=["tid1"],
        )
        assert cfg.get_isolation_strategy_for_tenant("other") == IsolationStrategy.RLS


class TestStr:
    def test_masks_password_in_url(self):
        cfg = _cfg()
        s = str(cfg)
        assert "pass" not in s

    def test_masks_jwt_secret(self):
        cfg = _cfg(jwt_secret="a" * 32)
        s = str(cfg)
        assert "a" * 32 not in s
