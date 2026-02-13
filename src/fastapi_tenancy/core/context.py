"""Context management for tenant information."""

from contextvars import ContextVar, Token
from types import TracebackType
from typing import Any

from fastapi_tenancy.core.exceptions import TenantNotFoundError
from fastapi_tenancy.core.types import Tenant

# Context variables for tenant information
_tenant_context: ContextVar[Tenant | None] = ContextVar("tenant", default=None)
_tenant_metadata: ContextVar[dict[str, Any]] = ContextVar("tenant_metadata")


class TenantContext:
    """Manages tenant context using async-safe context variables.

    This class provides a clean API for setting and retrieving the current
    tenant in an async-safe manner. It uses Python's contextvars which are
    automatically isolated per async task.

    Example:
        ```python
        # Set tenant context
        tenant = Tenant(id="1", identifier="acme", name="Acme Corp")
        TenantContext.set(tenant)

        # Get current tenant
        current = TenantContext.get()

        # Use context manager
        async with TenantContext.scope(tenant):
            # Tenant is set within this scope
            pass
        # Tenant is automatically cleared after scope
        ```
    """

    @staticmethod
    def set(tenant: Tenant) -> Token[Tenant | None]:
        """Set the current tenant in context.

        Args:
            tenant: Tenant to set as current

        Returns:
            Token that can be used to reset the context
        """
        return _tenant_context.set(tenant)

    @staticmethod
    def get() -> Tenant:
        """Get the current tenant from context.

        Returns:
            Current tenant

        Raises:
            TenantNotFoundError: If no tenant is set in context
        """
        tenant = _tenant_context.get()
        if tenant is None:
            msg = "No tenant found in current context"
            raise TenantNotFoundError(msg)
        return tenant

    @staticmethod
    def get_optional() -> Tenant | None:
        """Get the current tenant from context without raising an error.

        Returns:
            Current tenant or None if not set
        """
        return _tenant_context.get()

    @staticmethod
    def clear() -> None:
        """Clear the current tenant from context."""
        _tenant_context.set(None)
        _tenant_metadata.set({})

    @staticmethod
    def reset(token: Token[Tenant | None]) -> None:
        """Reset context to a previous state.

        Args:
            token: Token returned from set() or set_metadata()
        """
        _tenant_context.reset(token)

    @staticmethod
    def set_metadata(key: str, value: Any) -> None:  # noqa: ANN401
        """Set metadata for the current tenant context.

        This is useful for storing additional context-specific information
        like user permissions, request metadata, etc.

        Args:
            key: Metadata key
            value: Metadata value
        """
        metadata = _tenant_metadata.get({}).copy()
        metadata[key] = value
        _tenant_metadata.set(metadata)

    @staticmethod
    def get_metadata(key: str, default: Any = None) -> Any:  # noqa: ANN401
        """Get metadata from the current tenant context.

        Args:
            key: Metadata key
            default: Default value if key not found

        Returns:
            Metadata value or default
        """
        return _tenant_metadata.get().get(key, default)

    @staticmethod
    def clear_metadata() -> None:
        """Clear all metadata from context."""
        _tenant_metadata.set({})

    class scope:  # noqa: N801
        """Context manager for temporary tenant scope.

        This ensures tenant context is properly cleaned up even if
        an exception occurs.

        Example:
            ```python
            async with TenantContext.scope(tenant):
                # Code here has access to tenant
                await do_something()
            # Tenant is automatically cleared
            ```
        """

        def __init__(self, tenant: Tenant) -> None:
            """Initialize scope with tenant.

            Args:
                tenant: Tenant to set in scope
            """
            self.tenant = tenant
            self.token: Token[Tenant | None] | None = None
            self.metadata_token: Token[dict[str, Any]] | None = None

        async def __aenter__(self) -> Tenant:
            """Enter async context."""
            self.token = _tenant_context.set(self.tenant)
            self.metadata_token = _tenant_metadata.set({})
            return self.tenant

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> None:
            """Exit async context."""
            if self.token:
                _tenant_context.reset(self.token)
            if self.metadata_token:
                _tenant_metadata.reset(self.metadata_token)

        def __enter__(self) -> Tenant:
            """Enter sync context."""
            self.token = _tenant_context.set(self.tenant)
            self.metadata_token = _tenant_metadata.set({})
            return self.tenant

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: TracebackType | None,
        ) -> None:
            """Exit sync context."""
            if self.token:
                _tenant_context.reset(self.token)
            if self.metadata_token:
                _tenant_metadata.reset(self.metadata_token)


def get_current_tenant() -> Tenant:
    """Dependency function to get current tenant.

    This is a convenience function for use with FastAPI's dependency injection.

    Example:
        ```python
        from fastapi import Depends

        @app.get("/users")
        async def get_users(tenant: Tenant = Depends(get_current_tenant)):
            return {"tenant": tenant.identifier}
        ```

    Returns:
        Current tenant

    Raises:
        TenantNotFoundError: If no tenant is set in context
    """
    return TenantContext.get()


def get_current_tenant_optional() -> Tenant | None:
    """Dependency function to optionally get current tenant.

    This is useful for endpoints that can work with or without a tenant.

    Returns:
        Current tenant or None
    """
    return TenantContext.get_optional()
