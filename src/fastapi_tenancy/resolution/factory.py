"""Factory for creating tenant resolver instances."""

from typing import TYPE_CHECKING

from fastapi_tenancy.core.config import TenancyConfig
from fastapi_tenancy.core.types import ResolutionStrategy
from fastapi_tenancy.resolution.base import BaseTenantResolver
from fastapi_tenancy.storage.tenant_store import TenantStore

if TYPE_CHECKING:
    from collections.abc import Callable


class ResolverFactory:
    """Factory for creating tenant resolver instances."""

    @staticmethod
    def create(
        strategy: ResolutionStrategy,
        config: TenancyConfig,
        tenant_store: TenantStore,
    ) -> BaseTenantResolver:
        """Create tenant resolver based on strategy."""
        from fastapi_tenancy.resolution.header import HeaderTenantResolver
        from fastapi_tenancy.resolution.jwt import JWTTenantResolver
        from fastapi_tenancy.resolution.path import PathTenantResolver
        from fastapi_tenancy.resolution.subdomain import SubdomainTenantResolver

        resolvers:dict[ResolutionStrategy, Callable[[], BaseTenantResolver]] = {
            ResolutionStrategy.HEADER: lambda: HeaderTenantResolver(
                header_name=config.tenant_header_name,
                tenant_store=tenant_store,
            ),
            ResolutionStrategy.SUBDOMAIN: lambda: SubdomainTenantResolver(
                domain_suffix=config.domain_suffix or "",
                tenant_store=tenant_store,
            ),
            ResolutionStrategy.PATH: lambda: PathTenantResolver(
                path_prefix=config.path_prefix,
                tenant_store=tenant_store,
            ),
            ResolutionStrategy.JWT: lambda: JWTTenantResolver(
                secret=config.jwt_secret or "",
                algorithm=config.jwt_algorithm,
                tenant_claim=config.jwt_tenant_claim,
                tenant_store=tenant_store,
            ),
        }

        resolver_factory = resolvers.get(strategy)
        if not resolver_factory:
            raise ValueError(f"Unsupported resolution strategy: {strategy}")

        return resolver_factory()


__all__ = ["ResolverFactory"]
