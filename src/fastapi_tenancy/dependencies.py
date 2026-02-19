"""FastAPI dependency-injection helpers for multi-tenant applications.

Changes from v0.1.0
-------------------
- ``get_tenant_db`` no longer re-checks ``tenant.is_active()`` — the
  middleware already validates this before a request reaches any handler.
  The double-check was dead code that added confusion without adding safety.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, Request, status

from fastapi_tenancy.core.context import get_current_tenant
from fastapi_tenancy.core.types import Tenant, TenantConfig

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession


async def get_tenant_db(
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
    request: Request,
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session scoped to the current tenant.

    The session's ``search_path`` / connection target is set automatically
    by the configured :class:`~fastapi_tenancy.isolation.base.BaseIsolationProvider`.

    Note: Active-tenant validation is handled upstream by
    :class:`~fastapi_tenancy.middleware.tenancy.TenancyMiddleware`.
    This dependency does NOT re-validate to avoid redundancy.

    Example
    -------
    .. code-block:: python

        @app.get("/users")
        async def list_users(
            session: AsyncSession = Depends(get_tenant_db),
        ):
            result = await session.execute(select(User))
            return result.scalars().all()
    """
    isolation_provider = getattr(request.app.state, "isolation_provider", None)
    if isolation_provider is None:
        raise RuntimeError(
            "isolation_provider not found on app.state. "
            "Did you forget to use TenancyManager.create_lifespan()?"
        )
    async with isolation_provider.get_session(tenant) as session:
        yield session


async def require_active_tenant(
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
) -> Tenant:
    """Return the current tenant, raising 403 if inactive.

    Use this in routes that bypass the standard middleware (e.g. webhooks,
    admin-only routes with a different skip-path configuration).
    """
    if not tenant.is_active():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tenant {tenant.identifier!r} is {tenant.status.value}",
        )
    return tenant


async def get_tenant_config(
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
) -> TenantConfig:
    """Return a :class:`~fastapi_tenancy.core.types.TenantConfig` hydrated
    from the tenant's metadata blob.

    Example
    -------
    .. code-block:: python

        @app.get("/config")
        async def show_config(cfg: TenantConfig = Depends(get_tenant_config)):
            return cfg.model_dump()
    """
    return TenantConfig(
        max_users=tenant.metadata.get("max_users"),
        max_storage_gb=tenant.metadata.get("max_storage_gb"),
        features_enabled=tenant.metadata.get("features_enabled", []),
        rate_limit_per_minute=tenant.metadata.get("rate_limit_per_minute", 100),
        custom_settings=tenant.metadata.get("custom_settings", {}),
    )


__all__ = ["get_tenant_config", "get_tenant_db", "require_active_tenant"]
