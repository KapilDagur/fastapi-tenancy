"""Context management for tenant information using async-safe context variables.

This module provides thread-safe and async-safe tenant context management using
Python's contextvars module, which automatically handles context isolation
across async tasks.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any

from fastapi_tenancy.core.exceptions import TenantNotFoundError

if TYPE_CHECKING:
    from fastapi_tenancy.core.types import Tenant

# Context variables for tenant information
# These are automatically isolated per async task
_tenant_context: ContextVar[Tenant | None] = ContextVar("tenant", default=None)
_tenant_metadata: ContextVar[dict[str, Any] | None] = ContextVar("tenant_metadata", default=None)


class TenantContext:
    """Manages tenant context using async-safe context variables.

    This class provides a clean API for setting and retrieving the current
    tenant in an async-safe manner. It uses Python's contextvars which are
    automatically isolated per async task, making it safe for concurrent requests.

    The context is automatically cleared when async tasks complete, preventing
    data leakage between requests.

    Example:
        ```python
        # In middleware
        tenant = await resolve_tenant(request)
        TenantContext.set(tenant)

        # In route handler
        current_tenant = TenantContext.get()

        # Using context manager
        async with TenantContext.scope(tenant):
            # Tenant is set within this scope
            await process_tenant_data()
        # Tenant is automatically cleared after scope
        ```

    Note:
        Do not share TenantContext across different async tasks manually.
        Each async task (request) gets its own isolated context automatically.
    """

    @staticmethod
    def set(tenant: Tenant) -> Token[Tenant | None]:
        """Set the current tenant in context.

        Args:
            tenant: Tenant to set as current

        Returns:
            Token that can be used to reset the context later

        Example:
            ```python
            tenant = Tenant(id="123", identifier="acme", name="Acme")
            token = TenantContext.set(tenant)
            # ... do work ...
            TenantContext.reset(token)  # Restore previous state
            ```
        """
        return _tenant_context.set(tenant)

    @staticmethod
    def get() -> Tenant:
        """Get the current tenant from context.

        Returns:
            Current tenant

        Raises:
            TenantNotFoundError: If no tenant is set in current context

        Example:
            ```python
            try:
                tenant = TenantContext.get()
                print(f"Current tenant: {tenant.name}")
            except TenantNotFoundError:
                print("No tenant in context")
            ```
        """
        tenant = _tenant_context.get()
        if tenant is None:
            raise TenantNotFoundError("No tenant found in current context")
        return tenant

    @staticmethod
    def get_optional() -> Tenant | None:
        """Get the current tenant from context without raising an error.

        This is useful for endpoints that can work with or without a tenant,
        or for optional tenant-aware features.

        Returns:
            Current tenant or None if not set

        Example:
            ```python
            tenant = TenantContext.get_optional()
            if tenant:
                # Apply tenant-specific logic
                apply_tenant_theme(tenant)
            else:
                # Use default behavior
                apply_default_theme()
            ```
        """
        return _tenant_context.get()

    @staticmethod
    def clear() -> None:
        """Clear the current tenant from context.

        This resets both the tenant and its metadata. Typically called
        automatically by middleware after request processing.

        Example:
            ```python
            TenantContext.clear()
            # Both tenant and metadata are now None
            ```
        """
        _tenant_context.set(None)
        _tenant_metadata.set(None)

    @staticmethod
    def reset(token: Token[Tenant | None]) -> None:
        """Reset context to a previous state using a token.

        Args:
            token: Token returned from set() or set_metadata()

        Example:
            ```python
            token = TenantContext.set(tenant1)
            # ... work with tenant1 ...
            TenantContext.set(tenant2)
            # ... work with tenant2 ...
            TenantContext.reset(token)  # Back to tenant1
            ```
        """
        _tenant_context.reset(token)

    @staticmethod
    def set_metadata(key: str, value: Any) -> None:
        """Set metadata for the current tenant context.

        This is useful for storing additional context-specific information
        like user permissions, request metadata, feature flags, etc.

        Metadata is isolated per tenant context and automatically cleared
        when the context is cleared.

        Args:
            key: Metadata key
            value: Metadata value (any type)

        Example:
            ```python
            TenantContext.set_metadata("user_id", "user-123")
            TenantContext.set_metadata("permissions", ["read", "write"])
            TenantContext.set_metadata("request_id", uuid4())
            ```
        """
        existing = _tenant_metadata.get(None)
        metadata = dict(existing) if existing is not None else {}
        metadata[key] = value
        _tenant_metadata.set(metadata)

    @staticmethod
    def get_metadata(key: str, default: Any = None) -> Any:
        """Get metadata from the current tenant context.

        Args:
            key: Metadata key
            default: Default value if key not found

        Returns:
            Metadata value or default if not found

        Example:
            ```python
            user_id = TenantContext.get_metadata("user_id")
            permissions = TenantContext.get_metadata("permissions", [])
            ```
        """
        meta = _tenant_metadata.get(None)
        return meta.get(key, default) if meta is not None else default

    @staticmethod
    def get_all_metadata() -> dict[str, Any]:
        """Get all metadata from the current tenant context.

        Returns:
            Dictionary of all metadata

        Example:
            ```python
            metadata = TenantContext.get_all_metadata()
            print(f"Request ID: {metadata.get('request_id')}")
            print(f"User ID: {metadata.get('user_id')}")
            ```
        """
        meta = _tenant_metadata.get(None)
        return dict(meta) if meta is not None else {}

    @staticmethod
    def clear_metadata() -> None:
        """Clear all metadata from context.

        Keeps the tenant set, only clears metadata.

        Example:
            ```python
            TenantContext.clear_metadata()
            # Tenant still set, but metadata is empty
            ```
        """
        _tenant_metadata.set(None)

    class scope:
        """Context manager for temporary tenant scope.

        This ensures tenant context is properly cleaned up even if
        an exception occurs. Supports both async and sync contexts.

        Example:
            ```python
            # Async usage
            async with TenantContext.scope(tenant):
                # Code here has access to tenant
                await do_something()
            # Tenant is automatically cleared

            # Sync usage
            with TenantContext.scope(tenant):
                # Synchronous code
                do_something_sync()
            ```
        """

        def __init__(self, tenant: Tenant) -> None:
            """Initialize scope with tenant.

            Args:
                tenant: Tenant to set in scope
            """
            self.tenant = tenant
            self.token: Token[Tenant | None] | None = None
            self.metadata_token: Token[dict[str, Any] | None] | None = None

        async def __aenter__(self) -> Tenant:
            """Enter async context.

            Returns:
                The tenant that was set
            """
            self.token = _tenant_context.set(self.tenant)
            self.metadata_token = _tenant_metadata.set(None)
            return self.tenant

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: Any,
        ) -> None:
            """Exit async context.

            Context is cleared regardless of whether an exception occurred.
            """
            if self.token:
                _tenant_context.reset(self.token)
            if self.metadata_token:
                _tenant_metadata.reset(self.metadata_token)

        def __enter__(self) -> Tenant:
            """Enter sync context.

            Returns:
                The tenant that was set
            """
            self.token = _tenant_context.set(self.tenant)
            self.metadata_token = _tenant_metadata.set(None)
            return self.tenant

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: Any,
        ) -> None:
            """Exit sync context.

            Context is cleared regardless of whether an exception occurred.
            """
            if self.token:
                _tenant_context.reset(self.token)
            if self.metadata_token:
                _tenant_metadata.reset(self.metadata_token)


def get_current_tenant() -> Tenant:
    """Dependency function to get current tenant for FastAPI.

    This is a convenience function for use with FastAPI's dependency injection
    system. It delegates to TenantContext.get().

    Returns:
        Current tenant

    Raises:
        TenantNotFoundError: If no tenant is set in context

    Example:
        ```python
        from fastapi import Depends, FastAPI

        app = FastAPI()

        @app.get("/users")
        async def get_users(tenant: Tenant = Depends(get_current_tenant)):
            return {"tenant": tenant.identifier, "users": [...]}
        ```
    """
    return TenantContext.get()

def get_current_tenant_optional() -> Tenant | None:
    """Dependency function to optionally get current tenant.

    This is useful for endpoints that can work with or without a tenant,
    such as public endpoints or admin endpoints.

    Returns:
        Current tenant or None if not set

    Example:
        ```python
        from fastapi import Depends, FastAPI

        app = FastAPI()

        @app.get("/stats")
        async def get_stats(tenant: Tenant | None = Depends(get_current_tenant_optional)):
            if tenant:
                # Return tenant-specific stats
                return get_tenant_stats(tenant)
            else:
                # Return global stats
                return get_global_stats()
        ```
    """
    return TenantContext.get_optional()


__all__ = [
    "TenantContext",
    "get_current_tenant",
    "get_current_tenant_optional",
]
