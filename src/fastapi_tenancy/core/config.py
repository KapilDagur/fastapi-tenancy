"""Configuration management for FastAPI Tenancy.

This module provides comprehensive configuration with environment variable support,
validation, and type safety using Pydantic Settings.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from fastapi_tenancy.core.types import IsolationStrategy, ResolutionStrategy


class TenancyConfig(BaseSettings):
    """Main configuration for FastAPI Tenancy.

    All settings can be configured via environment variables with TENANCY_ prefix.
    Supports .env file loading for local development.

    Example:
        ```python
        # Using environment variables
        # TENANCY_RESOLUTION_STRATEGY=header
        # TENANCY_ISOLATION_STRATEGY=schema
        # TENANCY_DATABASE_URL=postgresql+asyncpg://...

        config = TenancyConfig()

        # Or programmatically
        config = TenancyConfig(
            database_url="postgresql+asyncpg://localhost/db",
            resolution_strategy="header",
            isolation_strategy="schema",
        )
        ```

    Attributes:
        resolution_strategy: Strategy for resolving tenant from requests
        isolation_strategy: Strategy for isolating tenant data
        database_url: Primary database connection URL
        redis_url: Redis connection URL for caching
        cache_enabled: Enable/disable caching layer
        enable_metrics: Enable metrics collection
        enable_audit_logging: Enable audit logging
    """

    model_config = SettingsConfigDict(
        env_prefix="TENANCY_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def __str__(self) -> str:
        """String representation with masked secrets."""
        result = super().__repr__()
        # Mask passwords in URLs
        result = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", result)
        # Mask secret values
        result = re.sub(
            r"(jwt_secret|encryption_key|secret|password|key)=(?:'[^']*'|[^\s,)]+)",
            r"\1='***'",
            result,
            flags=re.IGNORECASE,
        )
        return result

    #########################
    # Core Tenancy Settings #
    #########################

    resolution_strategy: ResolutionStrategy = Field(
        default=ResolutionStrategy.HEADER,
        description="Strategy for resolving tenant from requests",
    )

    isolation_strategy: IsolationStrategy = Field(
        default=IsolationStrategy.SCHEMA,
        description="Strategy for isolating tenant data",
    )

    ##########################
    # Database Configuration #
    ##########################

    database_url: str = Field(
        ...,
        description="Primary database connection URL (required)",
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
        description="Pool checkout timeout in seconds",
    )

    database_pool_recycle: int = Field(
        default=3600,
        ge=60,
        description="Connection recycle time in seconds (prevents stale connections)",
    )

    database_echo: bool = Field(
        default=False,
        description="Enable SQL query logging (use only in development)",
    )

    # Database per Tenant Settings (for DATABASE isolation strategy)
    database_url_template: str | None = Field(
        default=None,
        description="Template for per-tenant database URLs. Use {tenant_id} or {database_name}",
    )

    #######################
    # Cache Configuration #
    #######################

    redis_url: str | None = Field(
        default=None,
        description="Redis connection URL for caching (optional)",
    )

    cache_ttl: int = Field(
        default=3600,
        ge=0,
        description="Default cache TTL in seconds",
    )

    cache_enabled: bool = Field(
        default=False,  # Requires redis_url — off by default to avoid runtime errors
        description="Enable caching layer (requires redis_url)",
    )

    ################################
    # Resolution Strategy Settings #
    ################################

    tenant_header_name: str = Field(
        default="X-Tenant-ID",
        description="Header name for tenant resolution (HEADER strategy)",
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
        description="Secret key for JWT validation (required for JWT strategy)",
    )

    jwt_algorithm: str = Field(
        default="HS256",
        description="JWT signing algorithm",
    )

    jwt_tenant_claim: str = Field(
        default="tenant_id",
        description="JWT claim containing tenant ID",
    )

    #####################
    # Security Settings #
    #####################

    enable_rate_limiting: bool = Field(
        default=True,
        description="Enable per-tenant rate limiting",
    )

    rate_limit_per_minute: int = Field(
        default=100,
        ge=1,
        le=10000,
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
        description="Encryption key for data at rest (base64 encoded, 32 bytes)",
    )

    #####################
    # Tenant Management #
    #####################

    allow_tenant_registration: bool = Field(
        default=False,
        description="Allow self-service tenant registration",
    )

    max_tenants: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of tenants allowed (None = unlimited)",
    )

    default_tenant_status: Literal["active", "suspended", "provisioning"] = Field(
        default="active",
        description="Default status for new tenants",
    )

    ########################
    # Performance Settings #
    ########################

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
        description="Enable Prometheus metrics collection",
    )

    ############################################################
    # Hybrid Strategy Settings (for HYBRID isolation strategy) #
    ############################################################

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

    #################################################
    # Schema Naming (for SCHEMA isolation strategy) #
    #################################################

    schema_prefix: str = Field(
        default="tenant_",
        description="Prefix for tenant schema names",
    )

    public_schema: str = Field(
        default="public",
        description="Public/shared schema name",
    )

    #################
    # Feature Flags #
    #################

    enable_tenant_suspend: bool = Field(
        default=True,
        description="Enable tenant suspension feature",
    )

    enable_soft_delete: bool = Field(
        default=True,
        description="Enable soft delete for tenants",
    )

    ##############
    # Validators #
    ##############

    @field_validator("database_url", mode="before")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        """Validate database URL and warn about sync drivers.

        Accepts any SQLAlchemy-compatible URL.  Strips any trailing slash that
        Pydantic v2's ``AnyUrl`` type used to append (Pydantic v2 normalises
        URLs; using plain ``str`` avoids this but we guard defensively).
        Emits a warning when a synchronous driver scheme is detected.

        Args:
            v: Database URL to validate

        Returns:
            Validated, normalised URL string
        """
        import warnings

        from fastapi_tenancy.utils.db_compat import detect_dialect

        url_str = str(v).rstrip("/")
        detect_dialect(url_str)

        _SYNC_ONLY_SCHEMES = ("postgresql://", "sqlite://", "mysql://", "mssql://")
        if any(url_str.startswith(s) for s in _SYNC_ONLY_SCHEMES):
            warnings.warn(
                "Database URL uses a synchronous driver scheme. "
                "Use an async driver instead (e.g. postgresql+asyncpg, "
                "sqlite+aiosqlite, mysql+aiomysql).",
                stacklevel=4,
            )
        return url_str

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, v: str | None, info: ValidationInfo) -> str | None:
        """Validate JWT secret is provided when using JWT resolution.

        Args:
            v: JWT secret value
            info: Validation context

        Returns:
            Validated JWT secret

        Raises:
            ValueError: If JWT strategy requires secret
        """
        values = info.data
        if values.get("resolution_strategy") == ResolutionStrategy.JWT and not v:
            raise ValueError("jwt_secret is required when using JWT resolution strategy")
        if v and len(v) < 32:
            raise ValueError("jwt_secret must be at least 32 characters long")
        return v

    @field_validator("domain_suffix")
    @classmethod
    def validate_domain_suffix(cls, v: str | None, info: ValidationInfo) -> str | None:
        """Validate domain suffix is provided when using subdomain resolution.

        Args:
            v: Domain suffix value
            info: Validation context

        Returns:
            Validated domain suffix

        Raises:
            ValueError: If subdomain strategy requires domain suffix
        """
        values = info.data
        if values.get("resolution_strategy") == ResolutionStrategy.SUBDOMAIN and not v:
            raise ValueError("domain_suffix is required when using subdomain resolution strategy")
        return v

    @field_validator("encryption_key")
    @classmethod
    def validate_encryption_key(cls, v: str | None, info: ValidationInfo) -> str | None:
        """Validate encryption key is provided when encryption is enabled.

        Args:
            v: Encryption key value
            info: Validation context

        Returns:
            Validated encryption key

        Raises:
            ValueError: If encryption enabled but no key
        """
        values = info.data
        if values.get("enable_encryption") and not v:
            raise ValueError("encryption_key is required when encryption is enabled")
        if v and len(v) < 32:
            raise ValueError("encryption_key must be at least 32 characters (base64 encoded)")
        return v

    @field_validator("schema_prefix")
    @classmethod
    def validate_schema_prefix(cls, v: str) -> str:
        """Validate schema prefix format.

        Args:
            v: Schema prefix value

        Returns:
            Validated schema prefix

        Raises:
            ValueError: If prefix format is invalid
        """
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(
                "schema_prefix must start with letter and contain only lowercase letters, "
                "numbers, and underscores"
            )
        return v

    ##################
    # Helper Methods #
    ##################

    def get_schema_name(self, tenant_id: str) -> str:
        """Get schema name for a tenant.

        Args:
            tenant_id: Tenant identifier

        Returns:
            Schema name for the tenant (e.g., 'tenant_acme')

        Raises:
            ValueError: If tenant_id is invalid

        Example:
            ```python
            schema = config.get_schema_name("acme-corp")
            # Returns: "tenant_acme_corp"
            ```
        """
        # Validate tenant_id to prevent SQL injection
        from fastapi_tenancy.utils.validation import validate_tenant_identifier

        if not validate_tenant_identifier(tenant_id):
            raise ValueError(f"Invalid tenant identifier: {tenant_id}")

        # Sanitize for schema name (replace hyphens with underscores)
        sanitized = tenant_id.replace("-", "_").replace(".", "_")
        return f"{self.schema_prefix}{sanitized}"

    def get_database_url_for_tenant(self, tenant_id: str) -> str:
        """Get database URL for a tenant (for DATABASE isolation strategy).

        Args:
            tenant_id: Tenant identifier

        Returns:
            Database URL for the tenant

        Example:
            ```python
            url = config.get_database_url_for_tenant("acme-corp")
            ```
        """
        if self.database_url_template:
            # Sanitize tenant_id for database name
            db_name = tenant_id.replace("-", "_").replace(".", "_").lower()
            return self.database_url_template.format(
                tenant_id=tenant_id, database_name=f"tenant_{db_name}_db"
            )
        return str(self.database_url)

    def is_premium_tenant(self, tenant_id: str) -> bool:
        """Check if tenant is premium.

        Args:
            tenant_id: Tenant identifier

        Returns:
            True if tenant is in premium list

        Example:
            ```python
            if config.is_premium_tenant("acme-corp"):
                # Use premium isolation strategy
            ```
        """
        return tenant_id in self.premium_tenants

    def get_isolation_strategy_for_tenant(self, tenant_id: str) -> IsolationStrategy:
        """Get isolation strategy for a specific tenant.

        For HYBRID strategy, returns the appropriate strategy based on tenant tier.
        For other strategies, returns the configured strategy.

        Args:
            tenant_id: Tenant identifier

        Returns:
            Isolation strategy for the tenant

        Example:
            ```python
            strategy = config.get_isolation_strategy_for_tenant("acme-corp")
            # Returns: IsolationStrategy.SCHEMA (if premium)
            # or IsolationStrategy.RLS (if standard)
            ```
        """
        if self.isolation_strategy != IsolationStrategy.HYBRID:
            return self.isolation_strategy

        return (
            self.premium_isolation_strategy
            if self.is_premium_tenant(tenant_id)
            else self.standard_isolation_strategy
        )

    def model_post_init(self, __context: object) -> None:
        """Run cross-field validation after model construction.

        Automatically calls :meth:`validate_configuration` so misconfigured
        combinations (e.g. ``cache_enabled=True`` without a ``redis_url``) are
        caught at construction time rather than at runtime.
        """
        self.validate_configuration()

    def validate_configuration(self) -> None:
        """Validate complete configuration consistency.

        Raises:
            ValueError: If configuration is inconsistent

        Example:
            ```python
            config = TenancyConfig(...)
            config.validate_configuration()  # Raises if invalid
            ```
        """
        # Check cache configuration
        if self.cache_enabled and not self.redis_url:
            raise ValueError("cache_enabled requires redis_url to be set")

        # Check hybrid strategy
        if self.isolation_strategy == IsolationStrategy.HYBRID:  # noqa: SIM102
            if self.premium_isolation_strategy == self.standard_isolation_strategy:
                raise ValueError(
                    "Hybrid strategy requires different isolation strategies "
                    "for premium and standard tenants"
                )

        # Check database template for database isolation
        if self.isolation_strategy == IsolationStrategy.DATABASE:  # noqa: SIM102
            if not self.database_url_template:
                raise ValueError("DATABASE isolation requires database_url_template")


__all__ = ["TenancyConfig"]
