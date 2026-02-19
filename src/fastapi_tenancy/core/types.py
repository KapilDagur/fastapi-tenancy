"""Core types, protocols, and data models for FastAPI Tenancy.

Changes from v0.1.0
-------------------
- TenantMetrics: added ConfigDict(frozen=True) for consistency; all fields
  properly annotated.
- Removed redundant quoted annotations (from __future__ import annotations
  already makes all annotations strings under PEP 563, so quoting is noise).
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class TenantStatus(StrEnum):
    """Tenant lifecycle status."""
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"
    PROVISIONING = "provisioning"


class IsolationStrategy(StrEnum):
    """Data isolation strategy."""
    SCHEMA = "schema"
    DATABASE = "database"
    RLS = "rls"
    HYBRID = "hybrid"


class ResolutionStrategy(StrEnum):
    """Tenant resolution strategy."""
    HEADER = "header"
    SUBDOMAIN = "subdomain"
    PATH = "path"
    JWT = "jwt"
    CUSTOM = "custom"


class Tenant(BaseModel):
    """Immutable tenant domain model.

    Frozen to prevent accidental mutations — use ``model_copy(update={...})``
    to create modified versions.

    Example
    -------
    .. code-block:: python

        tenant = Tenant(
            id="tenant-123",
            identifier="acme-corp",
            name="Acme Corporation",
            status=TenantStatus.ACTIVE,
        )
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
                    "metadata": {"plan": "enterprise", "max_users": 100},
                }
            ]
        },
    )

    id: str = Field(..., description="Unique tenant identifier", min_length=1, max_length=255)
    identifier: str = Field(
        ..., description="Human-readable slug identifier", min_length=1, max_length=255
    )
    name: str = Field(..., description="Tenant display name", min_length=1, max_length=255)
    status: TenantStatus = Field(default=TenantStatus.ACTIVE, description="Lifecycle status")
    isolation_strategy: IsolationStrategy | None = Field(
        default=None, description="Per-tenant isolation override"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Flexible metadata storage"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Creation timestamp (UTC)",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Last update timestamp (UTC)",
    )
    database_url: str | None = Field(default=None, description="DB URL for DATABASE isolation")
    schema_name: str | None = Field(default=None, description="Schema name for SCHEMA isolation")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Tenant):
            return NotImplemented
        return self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)

    def is_active(self) -> bool:
        """Return True if tenant status is ACTIVE."""
        return self.status == TenantStatus.ACTIVE

    def model_dump_safe(self) -> dict[str, Any]:
        """Dump model with database_url masked — safe for logging."""
        data = self.model_dump()
        if data.get("database_url"):
            data["database_url"] = "***masked***"
        return data


class TenantConfig(BaseModel):
    """Tenant-specific configuration and quotas."""

    model_config = ConfigDict(frozen=True)

    max_users: int | None = Field(default=None, ge=0, description="Maximum users allowed")
    max_storage_gb: int | None = Field(default=None, ge=0, description="Maximum storage (GB)")
    features_enabled: list[str] = Field(default_factory=list, description="Enabled feature flags")
    rate_limit_per_minute: int = Field(
        default=100, ge=1, le=10000, description="API rate limit per minute"
    )
    custom_settings: dict[str, Any] = Field(
        default_factory=dict, description="Custom configuration"
    )


@runtime_checkable
class TenantResolver(Protocol):
    """Protocol for tenant resolution strategies."""

    async def resolve(self, request: Any) -> Tenant:
        """Resolve tenant from a FastAPI/Starlette ``Request``."""
        ...


def __getattr__(name: str) -> Any:
    """Lazy imports to avoid circular dependencies.

    - ``BaseTenantResolver``: ``core/types.py`` → ``resolution/base.py``
    - ``BaseIsolationProvider``: ``core/types.py`` → ``isolation/base.py``

    Both are deferred until first attribute access so static analysis and
    runtime see them as members of this module without creating import cycles.
    """
    if name == "BaseTenantResolver":
        from fastapi_tenancy.resolution.base import BaseTenantResolver
        return BaseTenantResolver
    if name == "BaseIsolationProvider":
        from fastapi_tenancy.isolation.base import BaseIsolationProvider
        return BaseIsolationProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class AuditLog(BaseModel):
    """Immutable audit log entry for tracking tenant operations."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(..., description="Tenant ID")
    user_id: str | None = Field(default=None, description="User ID if applicable")
    action: str = Field(..., description="Action performed", min_length=1)
    resource: str = Field(..., description="Resource type", min_length=1)
    resource_id: str | None = Field(default=None, description="Resource ID if applicable")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional context")
    ip_address: str | None = Field(default=None, description="Client IP address")
    user_agent: str | None = Field(default=None, description="Client user agent")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="Event timestamp (UTC)"
    )


class TenantMetrics(BaseModel):
    """Tenant usage metrics for monitoring and quota enforcement.

    Fix: now frozen and fully annotated — consistent with all other models.
    """

    model_config = ConfigDict(frozen=True)

    tenant_id: str = Field(..., description="Tenant ID")
    requests_count: int = Field(default=0, ge=0, description="Total requests handled")
    storage_bytes: int = Field(default=0, ge=0, description="Storage used in bytes")
    users_count: int = Field(default=0, ge=0, description="Number of active users")
    api_calls_today: int = Field(default=0, ge=0, description="API calls in the current day")
    last_activity: datetime | None = Field(default=None, description="Last activity timestamp")


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
