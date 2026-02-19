"""Factory for creating isolation provider instances."""

from fastapi_tenancy.core.config import TenancyConfig
from fastapi_tenancy.core.types import IsolationStrategy
from fastapi_tenancy.isolation.base import BaseIsolationProvider


class IsolationProviderFactory:
    """Factory for creating data isolation provider instances."""

    @staticmethod
    def create(
        strategy: IsolationStrategy,
        config: TenancyConfig,
    ) -> BaseIsolationProvider:
        """Create isolation provider based on strategy."""
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
        from fastapi_tenancy.isolation.hybrid import HybridIsolationProvider
        from fastapi_tenancy.isolation.rls import RLSIsolationProvider
        from fastapi_tenancy.isolation.schema import SchemaIsolationProvider

        providers = {
            IsolationStrategy.SCHEMA: SchemaIsolationProvider,
            IsolationStrategy.DATABASE: DatabaseIsolationProvider,
            IsolationStrategy.RLS: RLSIsolationProvider,
            IsolationStrategy.HYBRID: HybridIsolationProvider,
        }

        provider_class = providers.get(strategy)
        if not provider_class:
            raise ValueError(f"Unsupported isolation strategy: {strategy}")

        return provider_class(config)


__all__ = ["IsolationProviderFactory"]
