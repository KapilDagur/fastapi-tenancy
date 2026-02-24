"""Async-safe tenant context management using :mod:`contextvars`."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any

from fastapi_tenancy.core.exceptions import TenantNotFoundError

if TYPE_CHECKING:
    from fastapi_tenancy.core.types import Tenant

_tenant_ctx: ContextVar[Tenant | None] = ContextVar("tenant", default=None)
_metadata_ctx: ContextVar[dict[str, Any] | None] = ContextVar("tenant_metadata", default=None)


class TenantContext:
    """Namespace for async-safe per-request tenant context.

    All methods are static; this class is never instantiated.

    Usage in middleware::

        token = TenantContext.set(tenant)
        try:
            response = await call_next(request)
        finally:
            TenantContext.reset(token)

    Usage in route handlers / dependencies::

        tenant = TenantContext.get()
        tenant = TenantContext.get_optional()
    """

    @staticmethod
    def set(tenant: Tenant) -> Token[Tenant | None]:
        """Set *tenant* as the current request's tenant."""
        return _tenant_ctx.set(tenant)

    @staticmethod
    def get() -> Tenant:
        """Return the current tenant, raising if none is set.

        Raises:
            TenantNotFoundError: When called outside a tenancy-aware request.
        """
        tenant = _tenant_ctx.get()
        if tenant is None:
            raise TenantNotFoundError(
                "No tenant is set in the current execution context. "
                "Ensure the request passed through TenancyMiddleware."
            )
        return tenant

    @staticmethod
    def get_optional() -> Tenant | None:
        """Return the current tenant, or ``None`` if none is set."""
        return _tenant_ctx.get()

    @staticmethod
    def reset(token: Token[Tenant | None]) -> None:
        """Restore the tenant context to the state captured in *token*."""
        _tenant_ctx.reset(token)

    @staticmethod
    def clear() -> None:
        """Clear both the tenant and all metadata from the current context."""
        _tenant_ctx.set(None)
        _metadata_ctx.set(None)

    @staticmethod
    def set_metadata(key: str, value: object) -> None:
        """Attach a key-value pair to the current request's tenant context."""
        existing = _metadata_ctx.get()
        updated = dict(existing) if existing is not None else {}
        updated[key] = value
        _metadata_ctx.set(updated)

    @staticmethod
    def get_metadata(key: str, default: object = None) -> object:
        """Retrieve a metadata value from the current request context."""
        meta = _metadata_ctx.get()
        if meta is None:
            return default
        return meta.get(key, default)

    @staticmethod
    def get_all_metadata() -> dict[str, Any]:
        """Return a copy of all metadata in the current context."""
        meta = _metadata_ctx.get()
        return dict(meta) if meta is not None else {}

    @staticmethod
    def clear_metadata() -> None:
        """Clear all metadata while keeping the tenant set."""
        _metadata_ctx.set(None)

    class scope:
        """Context manager for temporary tenant scope.

        Sets a tenant for the duration of a ``with`` or ``async with``
        block and restores the previous state on exit::

            async with TenantContext.scope(tenant):
                await process_tenant_data()
        """

        def __init__(self, tenant: Tenant) -> None:
            self._tenant = tenant
            self._token: Token[Tenant | None] | None = None
            self._meta_token: Token[dict[str, Any] | None] | None = None

        async def __aenter__(self) -> Tenant:
            """Enter the async scope and return the active tenant."""
            self._token = _tenant_ctx.set(self._tenant)
            self._meta_token = _metadata_ctx.set(None)
            return self._tenant

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: object,
        ) -> None:
            """Exit the async scope and restore the previous context."""
            if self._token is not None:
                _tenant_ctx.reset(self._token)
            if self._meta_token is not None:
                _metadata_ctx.reset(self._meta_token)

        def __enter__(self) -> Tenant:
            """Enter the synchronous scope and return the active tenant."""
            self._token = _tenant_ctx.set(self._tenant)
            self._meta_token = _metadata_ctx.set(None)
            return self._tenant

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: object,
        ) -> None:
            """Exit the synchronous scope and restore the previous context."""
            if self._token is not None:
                _tenant_ctx.reset(self._token)
            if self._meta_token is not None:
                _metadata_ctx.reset(self._meta_token)


def get_current_tenant() -> Tenant:
    """FastAPI dependency — return the current tenant or raise.

    Inject via ``Depends`` in any route that requires a tenant::

        @app.get("/users")
        async def list_users(tenant: Tenant = Depends(get_current_tenant)):
            ...
    """
    return TenantContext.get()


def get_current_tenant_optional() -> Tenant | None:
    """FastAPI dependency — return the current tenant or ``None``."""
    return TenantContext.get_optional()


__all__ = [
    "TenantContext",
    "get_current_tenant",
    "get_current_tenant_optional",
]
