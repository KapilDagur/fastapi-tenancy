"""Custom exceptions for FastAPI Tenancy."""

from typing import Any


class TenancyError(Exception):
    """Base exception for all tenancy errors."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        """Initialize exception.

        Args:
            message: Error message
            details: Additional error details
        """
        self.message = message
        self.details = details or {}
        super().__init__(message)


class TenantNotFoundError(TenancyError):
    """Raised when tenant cannot be found or resolved."""

    def __init__(
        self,
        identifier: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize exception.

        Args:
            identifier: Tenant identifier that was not found
            details: Additional error details
        """
        message = (
            f"Tenant not found: {identifier}" if identifier else "Tenant not found"
        )
        super().__init__(message, details)
        self.identifier = identifier


class TenantResolutionError(TenancyError):
    """Raised when tenant resolution fails."""

    def __init__(
        self,
        reason: str,
        strategy: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize exception.

        Args:
            reason: Reason for resolution failure
            strategy: Resolution strategy that failed
            details: Additional error details
        """
        message = f"Tenant resolution failed: {reason}"
        if strategy:
            message += f" (strategy: {strategy})"
        super().__init__(message, details)
        self.reason = reason
        self.strategy = strategy


class TenantInactiveError(TenancyError):
    """Raised when tenant is inactive or suspended."""

    def __init__(
        self,
        tenant_id: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize exception.

        Args:
            tenant_id: ID of inactive tenant
            status: Current tenant status
            details: Additional error details
        """
        message = f"Tenant {tenant_id} is {status}"
        super().__init__(message, details)
        self.tenant_id = tenant_id
        self.status = status


class IsolationError(TenancyError):
    """Raised when data isolation fails."""

    def __init__(
        self,
        operation: str,
        tenant_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize exception.

        Args:
            operation: Operation that failed
            tenant_id: Tenant ID involved in operation
            details: Additional error details
        """
        message = f"Isolation operation failed: {operation}"
        if tenant_id:
            message += f" (tenant: {tenant_id})"
        super().__init__(message, details)
        self.operation = operation
        self.tenant_id = tenant_id


class ConfigurationError(TenancyError):
    """Raised when configuration is invalid."""

    def __init__(
        self,
        parameter: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize exception.

        Args:
            parameter: Configuration parameter that is invalid
            reason: Reason for invalidity
            details: Additional error details
        """
        message = f"Invalid configuration for '{parameter}': {reason}"
        super().__init__(message, details)
        self.parameter = parameter
        self.reason = reason


class MigrationError(TenancyError):
    """Raised when tenant migration fails."""

    def __init__(
        self,
        tenant_id: str,
        operation: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize exception.

        Args:
            tenant_id: Tenant ID
            operation: Migration operation
            reason: Failure reason
            details: Additional error details
        """
        message = (
            f"Migration failed for tenant {tenant_id} during {operation}: {reason}"
        )
        super().__init__(message, details)
        self.tenant_id = tenant_id
        self.operation = operation
        self.reason = reason


class RateLimitExceededError(TenancyError):
    """Raised when tenant exceeds rate limit."""

    def __init__(
        self,
        tenant_id: str,
        limit: int,
        window: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize exception.

        Args:
            tenant_id: Tenant ID
            limit: Rate limit threshold
            window: Time window (e.g., "1 minute")
            details: Additional error details
        """
        message = (
            f"Rate limit exceeded for tenant {tenant_id}: {limit} requests per {window}"
        )
        super().__init__(message, details)
        self.tenant_id = tenant_id
        self.limit = limit
        self.window = window


class TenantDataLeakageError(TenancyError):
    """Raised when potential data leakage is detected.

    This is a critical security error that should trigger alerts.
    """

    def __init__(
        self,
        operation: str,
        expected_tenant: str,
        actual_tenant: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize exception.

        Args:
            operation: Operation where leakage was detected
            expected_tenant: Expected tenant ID
            actual_tenant: Actual tenant ID found
            details: Additional error details
        """
        message = (
            f"SECURITY: Potential data leakage detected in {operation}. "
            f"Expected tenant: {expected_tenant}, got: {actual_tenant}"
        )
        super().__init__(message, details)
        self.operation = operation
        self.expected_tenant = expected_tenant
        self.actual_tenant = actual_tenant


class TenantQuotaExceededError(TenancyError):
    """Raised when tenant exceeds quota."""

    def __init__(
        self,
        tenant_id: str,
        quota_type: str,
        current: int | float,
        limit: int | float,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize exception.

        Args:
            tenant_id: Tenant ID
            quota_type: Type of quota exceeded
            current: Current usage
            limit: Quota limit
            details: Additional error details
        """
        message = (
            f"Quota exceeded for tenant {tenant_id}: "
            f"{quota_type} usage {current} exceeds limit {limit}"
        )
        super().__init__(message, details)
        self.tenant_id = tenant_id
        self.quota_type = quota_type
        self.current = current
        self.limit = limit


class DatabaseConnectionError(TenancyError):
    """Raised when database connection fails for a tenant."""

    def __init__(
        self,
        tenant_id: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialize exception.

        Args:
            tenant_id: Tenant ID
            reason: Connection failure reason
            details: Additional error details
        """
        message = f"Database connection failed for tenant {tenant_id}: {reason}"
        super().__init__(message, details)
        self.tenant_id = tenant_id
        self.reason = reason
