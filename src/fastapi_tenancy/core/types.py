"""Core types and protocols for FastAPI Tenancy."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from fastapi import Request


class TenantStatus(StrEnum):
    """Tenant status enumeration."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"
    PROVISIONING = "provisioning"


class IsolationStrategy(StrEnum):
    """Data isolation strategy enumeration."""

    SCHEMA = "schema"  # Schema per tenant
    DATABASE = "database"  # Database per tenant
    RLS = "rls"  # Row-level security
    HYBRID = "hybrid"  # Mix of strategies


class ResolutionStrategy(StrEnum):
    """Tenant resolution strategy enumeration."""

    HEADER = "header"  # X-Tenant-ID header
    SUBDOMAIN = "subdomain"  # subdomain.example.com
    PATH = "path"  # /tenants/{tenant_id}/...
    JWT = "jwt"  # JWT claim
    CUSTOM = "custom"  # Custom resolver


class Tenant(BaseModel):
    """Tenant model representing a single tenant."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: str = Field(..., description="Unique tenant identifier")
    identifier: str = Field(..., description="Human-readable identifier (slug)")
    name: str = Field(..., description="Tenant display name")
    status: TenantStatus = Field(default=TenantStatus.ACTIVE)
    isolation_strategy: IsolationStrategy = Field(
        default=IsolationStrategy.SCHEMA,
        description="Data isolation strategy for this tenant",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Database connection details (populated by isolation strategy)
    database_url: str | None = None
    schema_name: str | None = None

    def is_active(self) -> bool:
        """Check if tenant is active."""
        return self.status == TenantStatus.ACTIVE

    def __hash__(self) -> int:
        """Make tenant hashable for caching."""
        return hash(self.id)


class TenantConfig(BaseModel):
    """Tenant-specific configuration."""

    model_config = ConfigDict(frozen=True)

    max_users: int | None = None
    max_storage_gb: int | None = None
    features_enabled: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int = 100
    custom_settings: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class TenantResolver(Protocol):
    """Protocol for tenant resolution strategies."""

    async def resolve(self, request: Request) -> Tenant:
        """Resolve tenant from request.

        Args:
            request: FastAPI request object

        Returns:
            Resolved tenant

        Raises:
            TenantNotFoundError: If tenant cannot be resolved
        """
        ...


@runtime_checkable
class IsolationProvider(Protocol):
    """Protocol for data isolation providers."""

    async def get_session(self, tenant: Tenant) -> Any:  # noqa: ANN401
        """Get database session for tenant.

        Args:
            tenant: Tenant to get session for

        Returns:
            Database session scoped to tenant
        """
        ...

    async def apply_filters(self, query: Any, tenant: Tenant) -> Any:  # noqa: ANN401
        """Apply tenant filters to query.

        Args:
            query: SQLAlchemy query
            tenant: Current tenant

        Returns:
            Filtered query
        """
        ...

    async def initialize_tenant(self, tenant: Tenant) -> None:
        """Initialize tenant database/schema.

        Args:
            tenant: Tenant to initialize
        """
        ...

    async def destroy_tenant(self, tenant: Tenant) -> None:
        """Destroy tenant database/schema.

        Args:
            tenant: Tenant to destroy
        """
        ...


class BaseTenantResolver(ABC):
    """Base class for tenant resolvers."""

    @abstractmethod
    async def resolve(self, request: Request) -> Tenant:
        """Resolve tenant from request."""
        pass


class BaseIsolationProvider(ABC):
    """Base class for isolation providers."""

    @abstractmethod
    async def get_session(self, tenant: Tenant) -> Any:  # noqa: ANN401
        """Get database session for tenant."""
        pass

    @abstractmethod
    async def apply_filters(self, query: Any, tenant: Tenant) -> Any:  # noqa: ANN401
        """Apply tenant filters to query."""
        pass

    @abstractmethod
    async def initialize_tenant(self, tenant: Tenant) -> None:
        """Initialize tenant database/schema."""
        pass

    @abstractmethod
    async def destroy_tenant(self, tenant: Tenant) -> None:
        """Destroy tenant database/schema."""
        pass


class AuditLog(BaseModel):
    """Audit log entry."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    user_id: str | None = None
    action: str
    resource: str
    resource_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    ip_address: str | None = None
    user_agent: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class TenantMetrics(BaseModel):
    """Tenant usage metrics."""

    tenant_id: str
    requests_count: int = 0
    storage_bytes: int = 0
    users_count: int = 0
    api_calls_today: int = 0
    last_activity: datetime | None = None
