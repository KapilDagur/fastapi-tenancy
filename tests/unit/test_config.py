"""Unit tests for core configuration."""

import pytest
from pydantic import ValidationError

from fastapi_tenancy.core.config import TenancyConfig
from fastapi_tenancy.core.types import IsolationStrategy, ResolutionStrategy


class TestTenancyConfig:
    """Test suite for TenancyConfig."""

    def test_default_config(self) -> None:
        """Test default configuration values."""
        config = TenancyConfig(database_url="postgresql+asyncpg://test:test@localhost/test")  # type: ignore

        assert config.resolution_strategy == ResolutionStrategy.HEADER
        assert config.isolation_strategy == IsolationStrategy.SCHEMA
        assert config.database_pool_size == 20
        assert config.database_max_overflow == 40
        assert config.cache_ttl == 3600
        assert config.enable_rate_limiting is True
        assert config.rate_limit_per_minute == 100

    def test_custom_config(self) -> None:
        """Test custom configuration values."""
        config = TenancyConfig(
            database_url="postgresql+asyncpg://test:test@localhost/test",  # type: ignore
            resolution_strategy=ResolutionStrategy.SUBDOMAIN,
            domain_suffix=".example.com",
            isolation_strategy=IsolationStrategy.RLS,
            database_pool_size=50,
            enable_rate_limiting=False,
        )

        assert config.resolution_strategy == ResolutionStrategy.SUBDOMAIN
        assert config.domain_suffix == ".example.com"
        assert config.isolation_strategy == IsolationStrategy.RLS
        assert config.database_pool_size == 50
        assert config.enable_rate_limiting is False

    def test_database_url_validation(self) -> None:
        """Test database URL validation."""
        # Valid PostgreSQL URL
        config = TenancyConfig(database_url="postgresql://test:test@localhost/test")  # type: ignore
        assert config.database_url is not None

        # Valid async PostgreSQL URL
        config = TenancyConfig(database_url="postgresql+asyncpg://test:test@localhost/test")  # type: ignore
        assert config.database_url is not None

        # Invalid URL (not PostgreSQL)
        with pytest.raises(ValidationError):
            TenancyConfig(database_url="mysql://test:test@localhost/test")  # type: ignore

    def test_jwt_validation(self) -> None:
        """Test JWT secret validation."""
        # JWT strategy requires jwt_secret
        with pytest.raises(ValidationError):
            TenancyConfig(
                database_url="postgresql+asyncpg://test:test@localhost/test",  # type: ignore
                resolution_strategy=ResolutionStrategy.JWT,
                jwt_secret=None,
            )

        # JWT strategy with secret works
        config = TenancyConfig(
            database_url="postgresql+asyncpg://test:test@localhost/test",  # type: ignore
            resolution_strategy=ResolutionStrategy.JWT,
            jwt_secret="my-secret-key",  # noqa: S106
        )
        assert config.jwt_secret == "my-secret-key"  # noqa: S105

    def test_subdomain_validation(self) -> None:
        """Test subdomain resolution validation."""
        # Subdomain strategy requires domain_suffix
        with pytest.raises(ValidationError):
            TenancyConfig(
                database_url="postgresql+asyncpg://test:test@localhost/test",  # type: ignore
                resolution_strategy=ResolutionStrategy.SUBDOMAIN,
                domain_suffix=None,
            )

        # Subdomain strategy with domain suffix works
        config = TenancyConfig(
            database_url="postgresql+asyncpg://test:test@localhost/test",  # type: ignore
            resolution_strategy=ResolutionStrategy.SUBDOMAIN,
            domain_suffix=".example.com",
        )
        assert config.domain_suffix == ".example.com"

    def test_encryption_validation(self) -> None:
        """Test encryption key validation."""
        # Encryption enabled requires encryption_key
        with pytest.raises(ValidationError):
            TenancyConfig(
                database_url="postgresql+asyncpg://test:test@localhost/test",  # type: ignore
                enable_encryption=True,
                encryption_key=None,
            )

        # Encryption with key works
        config = TenancyConfig(
            database_url="postgresql+asyncpg://test:test@localhost/test",  # type: ignore
            enable_encryption=True,
            encryption_key="base64-encoded-key",
        )
        assert config.encryption_key == "base64-encoded-key"

    def test_get_schema_name(self) -> None:
        """Test schema name generation."""
        config = TenancyConfig(
            database_url="postgresql+asyncpg://test:test@localhost/test",  # type: ignore
            schema_prefix="tenant_",
        )

        assert config.get_schema_name("acme") == "tenant_acme"
        assert config.get_schema_name("globex") == "tenant_globex"

    def test_get_database_url_for_tenant(self) -> None:
        """Test database URL generation for tenant."""
        # Without template
        config = TenancyConfig(database_url="postgresql+asyncpg://test:test@localhost/test")  # type: ignore
        url = config.get_database_url_for_tenant("acme")
        assert url == "postgresql+asyncpg://test:test@localhost/test"

        # With template
        config = TenancyConfig(
            database_url="postgresql+asyncpg://test:test@localhost/test",  # type: ignore
            database_url_template="postgresql+asyncpg://test:test@localhost/{tenant_id}",
        )
        url = config.get_database_url_for_tenant("acme")
        assert url == "postgresql+asyncpg://test:test@localhost/acme"

    def test_premium_tenant_check(self) -> None:
        """Test premium tenant identification."""
        config = TenancyConfig(
            database_url="postgresql+asyncpg://test:test@localhost/test",  # type: ignore
            premium_tenants=["acme", "globex"],
        )

        assert config.is_premium_tenant("acme") is True
        assert config.is_premium_tenant("globex") is True
        assert config.is_premium_tenant("standard") is False

    def test_get_isolation_strategy_for_tenant(self) -> None:
        """Test isolation strategy selection for tenant."""
        # Non-hybrid strategy
        config = TenancyConfig(
            database_url="postgresql+asyncpg://test:test@localhost/test",  # type: ignore
            isolation_strategy=IsolationStrategy.SCHEMA,
        )
        assert config.get_isolation_strategy_for_tenant("acme") == IsolationStrategy.SCHEMA

        # Hybrid strategy - premium tenant
        config = TenancyConfig(
            database_url="postgresql+asyncpg://test:test@localhost/test",  # type: ignore
            isolation_strategy=IsolationStrategy.HYBRID,
            premium_tenants=["acme"],
            premium_isolation_strategy=IsolationStrategy.DATABASE,
            standard_isolation_strategy=IsolationStrategy.RLS,
        )
        assert config.get_isolation_strategy_for_tenant("acme") == IsolationStrategy.DATABASE

        # Hybrid strategy - standard tenant
        assert config.get_isolation_strategy_for_tenant("standard") == IsolationStrategy.RLS

    def test_pool_size_validation(self) -> None:
        """Test database pool size validation."""
        # Valid pool size
        config = TenancyConfig(
            database_url="postgresql+asyncpg://test:test@localhost/test",  # type: ignore
            database_pool_size=50,
        )
        assert config.database_pool_size == 50

        # Invalid pool size (too small)
        with pytest.raises(ValidationError):
            TenancyConfig(
                database_url="postgresql+asyncpg://test:test@localhost/test",  # type: ignore
                database_pool_size=0,
            )

        # Invalid pool size (too large)
        with pytest.raises(ValidationError):
            TenancyConfig(
                database_url="postgresql+asyncpg://test:test@localhost/test",  # type: ignore
                database_pool_size=200,
            )

    def test_environment_variable_loading(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test loading configuration from environment variables."""
        monkeypatch.setenv("TENANCY_DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
        monkeypatch.setenv("TENANCY_RESOLUTION_STRATEGY", "subdomain")
        monkeypatch.setenv("TENANCY_DOMAIN_SUFFIX", ".example.com")
        monkeypatch.setenv("TENANCY_DATABASE_POOL_SIZE", "30")

        config = TenancyConfig()  # type: ignore

        assert str(config.database_url) == "postgresql+asyncpg://test:test@localhost/test"
        assert config.resolution_strategy == ResolutionStrategy.SUBDOMAIN
        assert config.domain_suffix == ".example.com"
        assert config.database_pool_size == 30
