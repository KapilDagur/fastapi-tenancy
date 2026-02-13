"""Configuration management for FastAPI Tenancy."""

from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from fastapi_tenancy.core.types import IsolationStrategy, ResolutionStrategy


class TenancyConfig(BaseSettings):
    """Main configuration for FastAPI Tenancy.

    All settings can be configured via environment variables with TENANCY_ prefix.
    Example: TENANCY_RESOLUTION_STRATEGY=header
    """

    model_config = SettingsConfigDict(
        env_prefix="TENANCY_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Core Tenancy Settings
    resolution_strategy: ResolutionStrategy = Field(
        default=ResolutionStrategy.HEADER,
        description="Strategy for resolving tenant from requests",
    )
    isolation_strategy: IsolationStrategy = Field(
        default=IsolationStrategy.SCHEMA,
        description="Strategy for isolating tenant data",
    )

    # Database Configuration
    database_url: PostgresDsn = Field(
        ...,
        description="Primary database connection URL",
    )
    database_pool_size: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Database connection pool size",
    )
    database_max_overflow: int = Field(
        default=40,
        ge=0,
        le=200,
        description="Max overflow connections beyond pool size",
    )
    database_pool_timeout: int = Field(
        default=30,
        ge=1,
        description="Pool timeout in seconds",
    )
    database_pool_recycle: int = Field(
        default=3600,
        ge=60,
        description="Connection recycle time in seconds",
    )
    database_echo: bool = Field(
        default=False,
        description="Enable SQL query logging",
    )

    # Database per Tenant Settings (for DATABASE isolation strategy)
    database_url_template: str | None = Field(
        default=None,
        description="Template for per-tenant database URLs. Use {tenant_id} placeholder",
    )

    # Cache Configuration
    redis_url: RedisDsn | None = Field(
        default=None,
        description="Redis connection URL for caching",
    )
    cache_ttl: int = Field(
        default=3600,
        ge=0,
        description="Default cache TTL in seconds",
    )
    cache_enabled: bool = Field(
        default=True,
        description="Enable caching",
    )

    # Resolution Strategy Settings
    tenant_header_name: str = Field(
        default="X-Tenant-ID",
        description="Header name for tenant resolution",
    )
    domain_suffix: str | None = Field(
        default=None,
        description="Domain suffix for subdomain resolution (e.g., '.example.com')",
    )
    path_prefix: str = Field(
        default="/tenants",
        description="Path prefix for path-based resolution",
    )
    jwt_secret: str | None = Field(
        default=None,
        description="Secret key for JWT validation",
    )
    jwt_algorithm: str = Field(
        default="HS256",
        description="JWT algorithm",
    )
    jwt_tenant_claim: str = Field(
        default="tenant_id",
        description="JWT claim containing tenant ID",
    )

    # Security Settings
    enable_rate_limiting: bool = Field(
        default=True,
        description="Enable per-tenant rate limiting",
    )
    rate_limit_per_minute: int = Field(
        default=100,
        ge=1,
        description="Default rate limit per minute per tenant",
    )
    rate_limit_window: int = Field(
        default=60,
        ge=1,
        description="Rate limit window in seconds",
    )
    enable_audit_logging: bool = Field(
        default=True,
        description="Enable audit logging for tenant operations",
    )
    enable_encryption: bool = Field(
        default=False,
        description="Enable encryption for sensitive tenant data",
    )
    encryption_key: str | None = Field(
        default=None,
        description="Encryption key for data at rest (base64 encoded)",
    )

    # Tenant Management
    allow_tenant_registration: bool = Field(
        default=False,
        description="Allow self-service tenant registration",
    )
    max_tenants: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of tenants allowed",
    )
    default_tenant_status: Literal["active", "suspended", "provisioning"] = Field(
        default="active",
        description="Default status for new tenants",
    )

    # Performance Settings
    enable_query_logging: bool = Field(
        default=False,
        description="Enable slow query logging",
    )
    slow_query_threshold_ms: int = Field(
        default=1000,
        ge=0,
        description="Threshold for slow query logging in milliseconds",
    )
    enable_metrics: bool = Field(
        default=True,
        description="Enable metrics collection",
    )

    # Hybrid Strategy Settings (for HYBRID isolation strategy)
    premium_tenants: list[str] = Field(
        default_factory=list,
        description="List of premium tenant IDs that get dedicated resources",
    )
    premium_isolation_strategy: IsolationStrategy = Field(
        default=IsolationStrategy.SCHEMA,
        description="Isolation strategy for premium tenants",
    )
    standard_isolation_strategy: IsolationStrategy = Field(
        default=IsolationStrategy.RLS,
        description="Isolation strategy for standard tenants",
    )

    # Schema Naming (for SCHEMA isolation strategy)
    schema_prefix: str = Field(
        default="tenant_",
        description="Prefix for tenant schema names",
    )
    public_schema: str = Field(
        default="public",
        description="Public/shared schema name",
    )

    # Feature Flags
    enable_tenant_suspend: bool = Field(
        default=True,
        description="Enable tenant suspension feature",
    )
    enable_soft_delete: bool = Field(
        default=True,
        description="Enable soft delete for tenants",
    )

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: PostgresDsn) -> PostgresDsn:
        """Validate database URL is PostgreSQL."""
        if not str(v).startswith(("postgresql://", "postgresql+asyncpg://")):
            msg = "Only PostgreSQL databases are supported"
            raise ValueError(msg)
        return v

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, v: str | None, info: ValidationInfo) -> str | None:
        """Validate JWT secret is provided when using JWT resolution."""
        values = info.data
        if values.get("resolution_strategy") == ResolutionStrategy.JWT and not v:
            msg = "jwt_secret is required when using JWT resolution strategy"
            raise ValueError(msg)
        return v

    @field_validator("domain_suffix")
    @classmethod
    def validate_domain_suffix(cls, v: str | None, info: ValidationInfo) -> str | None:
        """Validate domain suffix is provided when using subdomain resolution."""
        values = info.data
        if values.get("resolution_strategy") == ResolutionStrategy.SUBDOMAIN and not v:
            msg = "domain_suffix is required when using subdomain resolution strategy"
            raise ValueError(msg)
        return v

    @field_validator("encryption_key")
    @classmethod
    def validate_encryption_key(cls, v: str | None, info: ValidationInfo) -> str | None:
        """Validate encryption key is provided when encryption is enabled."""
        values = info.data
        if values.get("enable_encryption") and not v:
            msg = "encryption_key is required when encryption is enabled"
            raise ValueError(msg)
        return v

    def get_schema_name(self, tenant_id: str) -> str:
        """Get schema name for a tenant.

        Args:
            tenant_id: Tenant identifier

        Returns:
            Schema name for the tenant
        """
        return f"{self.schema_prefix}{tenant_id}"

    def get_database_url_for_tenant(self, tenant_id: str) -> str:
        """Get database URL for a tenant (for DATABASE isolation strategy).

        Args:
            tenant_id: Tenant identifier

        Returns:
            Database URL for the tenant
        """
        if self.database_url_template:
            return self.database_url_template.format(tenant_id=tenant_id)
        return str(self.database_url)

    def is_premium_tenant(self, tenant_id: str) -> bool:
        """Check if tenant is premium.

        Args:
            tenant_id: Tenant identifier

        Returns:
            True if tenant is premium
        """
        return tenant_id in self.premium_tenants

    def get_isolation_strategy_for_tenant(self, tenant_id: str) -> IsolationStrategy:
        """Get isolation strategy for a specific tenant.

        Args:
            tenant_id: Tenant identifier

        Returns:
            Isolation strategy for the tenant
        """
        if self.isolation_strategy != IsolationStrategy.HYBRID:
            return self.isolation_strategy

        return (
            self.premium_isolation_strategy
            if self.is_premium_tenant(tenant_id)
            else self.standard_isolation_strategy
        )
