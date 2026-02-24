"""Custom exceptions for fastapi-tenancy.

All exceptions derive from :class:`TenancyError` so callers can catch the
entire family with a single ``except TenancyError`` clause.

Hierarchy::

    TenancyError
    ├── TenantNotFoundError
    ├── TenantResolutionError
    ├── TenantInactiveError
    ├── IsolationError
    ├── ConfigurationError
    ├── MigrationError
    ├── RateLimitExceededError
    ├── TenantDataLeakageError
    ├── TenantQuotaExceededError
    └── DatabaseConnectionError
"""

from __future__ import annotations

from typing import Any


class TenancyError(Exception):
    """Base exception for all fastapi-tenancy errors."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.message = message
        self.details: dict[str, Any] = details or {}
        super().__init__(message)

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} | details={self.details}"
        return self.message

    def __repr__(self) -> str:
        return f"{type(self).__name__}(message={self.message!r})"


class TenantNotFoundError(TenancyError):
    """Raised when a tenant cannot be located in the configured store."""

    def __init__(
        self,
        identifier: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        message = f"Tenant not found: {identifier!r}" if identifier else "Tenant not found"
        super().__init__(message, details)
        self.identifier = identifier


class TenantResolutionError(TenancyError):
    """Raised when the configured strategy cannot extract a tenant from a request."""

    def __init__(
        self,
        reason: str,
        strategy: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        message = f"Tenant resolution failed: {reason}"
        if strategy:
            message += f" (strategy: {strategy})"
        super().__init__(message, details)
        self.reason = reason
        self.strategy = strategy


class TenantInactiveError(TenancyError):
    """Raised when a resolved tenant is not in the ``ACTIVE`` status."""

    def __init__(
        self,
        tenant_id: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"Tenant {tenant_id!r} is {status}", details)
        self.tenant_id = tenant_id
        self.status = status


class IsolationError(TenancyError):
    """Raised when a data-isolation operation fails."""

    def __init__(
        self,
        operation: str,
        tenant_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        message = f"Isolation operation failed: {operation}"
        if tenant_id:
            message += f" (tenant: {tenant_id!r})"
        super().__init__(message, details)
        self.operation = operation
        self.tenant_id = tenant_id


class ConfigurationError(TenancyError):
    """Raised when :class:`~fastapi_tenancy.core.config.TenancyConfig` contains an invalid value."""

    def __init__(
        self,
        parameter: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"Invalid configuration for {parameter!r}: {reason}", details)
        self.parameter = parameter
        self.reason = reason


class MigrationError(TenancyError):
    """Raised when an Alembic migration operation fails for a tenant."""

    def __init__(
        self,
        tenant_id: str,
        operation: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            f"Migration failed for tenant {tenant_id!r} during {operation!r}: {reason}",
            details,
        )
        self.tenant_id = tenant_id
        self.operation = operation
        self.reason = reason


class RateLimitExceededError(TenancyError):
    """Raised when a tenant exceeds its configured request rate limit."""

    def __init__(
        self,
        tenant_id: str,
        limit: int,
        window: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            f"Rate limit exceeded for tenant {tenant_id!r}: {limit} requests per {window}",
            details,
        )
        self.tenant_id = tenant_id
        self.limit = limit
        self.window = window


class TenantDataLeakageError(TenancyError):
    """Raised when a potential cross-tenant data leakage is detected.

    This is a **critical security exception**. Any occurrence should trigger
    an immediate alert and halt the request.
    """

    def __init__(
        self,
        operation: str,
        expected_tenant: str,
        actual_tenant: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            f"SECURITY: potential data leakage in {operation!r} — "
            f"expected tenant {expected_tenant!r}, got {actual_tenant!r}",
            details,
        )
        self.operation = operation
        self.expected_tenant = expected_tenant
        self.actual_tenant = actual_tenant


class TenantQuotaExceededError(TenancyError):
    """Raised when a tenant exceeds a resource quota."""

    def __init__(
        self,
        tenant_id: str,
        quota_type: str,
        current: int | float,
        limit: int | float,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            f"Quota exceeded for tenant {tenant_id!r}: "
            f"{quota_type} usage {current} exceeds limit {limit}",
            details,
        )
        self.tenant_id = tenant_id
        self.quota_type = quota_type
        self.current = current
        self.limit = limit


class DatabaseConnectionError(TenancyError):
    """Raised when the library cannot establish a database connection for a tenant."""

    def __init__(
        self,
        tenant_id: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            f"Database connection failed for tenant {tenant_id!r}: {reason}",
            details,
        )
        self.tenant_id = tenant_id
        self.reason = reason


__all__ = [
    "ConfigurationError",
    "DatabaseConnectionError",
    "IsolationError",
    "MigrationError",
    "RateLimitExceededError",
    "TenancyError",
    "TenantDataLeakageError",
    "TenantInactiveError",
    "TenantNotFoundError",
    "TenantQuotaExceededError",
    "TenantResolutionError",
]
