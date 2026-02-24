"""Domain types, enumerations, and data models for fastapi-tenancy."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class TenantStatus(StrEnum):
    """Lifecycle status of a tenant."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"
    PROVISIONING = "provisioning"


class IsolationStrategy(StrEnum):
    """Data-isolation strategy applied to tenant requests."""

    SCHEMA = "schema"
    DATABASE = "database"
    RLS = "rls"
    HYBRID = "hybrid"


class ResolutionStrategy(StrEnum):
    """Method used to extract the tenant identifier from an HTTP request."""

    HEADER = "header"
    SUBDOMAIN = "subdomain"
    PATH = "path"
    JWT = "jwt"
    CUSTOM = "custom"


class Tenant(BaseModel):
    """Immutable tenant domain model.

    All instances are frozen. To produce a modified copy use :meth:`model_copy`::

        updated = tenant.model_copy(update={"status": TenantStatus.SUSPENDED})
    """

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "tenant-123",
                    "identifier": "acme-corp",
                    "name": "Acme Corporation",
                    "status": "active",
                    "isolation_strategy": "schema",
                    "metadata": {"plan": "enterprise", "max_users": 500},
                }
            ]
        },
    )

    id: str = Field(..., min_length=1, max_length=255)
    identifier: str = Field(..., min_length=1, max_length=255)
    name: str = Field(..., min_length=1, max_length=255)
    status: TenantStatus = Field(default=TenantStatus.ACTIVE)
    isolation_strategy: IsolationStrategy | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    database_url: str | None = Field(default=None)
    schema_name: str | None = Field(default=None)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Tenant):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)

    def is_active(self) -> bool:
        """Return ``True`` if status is :attr:`TenantStatus.ACTIVE`."""
        return self.status == TenantStatus.ACTIVE

    def model_dump_safe(self) -> dict[str, Any]:
        """Return a serialisable dict with ``database_url`` masked."""
        data = self.model_dump()
        if data.get("database_url"):
            data["database_url"] = "***masked***"
        return data


class TenantConfig(BaseModel):
    """Per-tenant quota and feature configuration."""

    model_config = ConfigDict(frozen=True)

    max_users: int | None = Field(default=None, ge=0)
    max_storage_gb: int | None = Field(default=None, ge=0)
    features_enabled: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int = Field(default=100, ge=1, le=10_000)
    custom_settings: dict[str, Any] = Field(default_factory=dict)


class AuditLog(BaseModel):
    """Immutable audit-log entry for tenant operations."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(...)
    user_id: str | None = Field(default=None)
    action: str = Field(..., min_length=1)
    resource: str = Field(..., min_length=1)
    resource_id: str | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)
    ip_address: str | None = Field(default=None)
    user_agent: str | None = Field(default=None)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TenantMetrics(BaseModel):
    """Snapshot of a tenant's usage metrics."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(...)
    requests_count: int = Field(default=0, ge=0)
    storage_bytes: int = Field(default=0, ge=0)
    users_count: int = Field(default=0, ge=0)
    api_calls_today: int = Field(default=0, ge=0)
    last_activity: datetime | None = Field(default=None)


@runtime_checkable
class TenantResolver(Protocol):
    """Structural type for tenant resolution strategies."""

    async def resolve(self, request: object) -> Tenant:
        """Resolve the current tenant from *request*."""
        ...


def __getattr__(name: str) -> Any:
    """Lazy-import extension points to break circular dependency chains."""
    if name == "BaseTenantResolver":
        from fastapi_tenancy.resolution.base import BaseTenantResolver

        return BaseTenantResolver

    if name == "BaseIsolationProvider":
        from fastapi_tenancy.isolation.base import BaseIsolationProvider

        return BaseIsolationProvider

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AuditLog",
    "IsolationStrategy",
    "ResolutionStrategy",
    "Tenant",
    "TenantConfig",
    "TenantMetrics",
    "TenantResolver",
    "TenantStatus",
]
