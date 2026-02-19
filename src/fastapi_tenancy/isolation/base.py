"""Base isolation provider implementation.

This module provides the base class for data isolation strategies.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

    from fastapi_tenancy.core.config import TenancyConfig
    from fastapi_tenancy.core.types import Tenant

logger = logging.getLogger(__name__)


class BaseIsolationProvider(ABC):
    """Abstract base class for data isolation strategies.

    All isolation providers must inherit from this class and implement
    the required methods. This provides a consistent interface for
    different isolation strategies.

    Isolation strategies determine how tenant data is separated:
    - Schema: Each tenant gets a separate PostgreSQL schema
    - Database: Each tenant gets a separate database
    - RLS: Row-Level Security policies filter data
    - Hybrid: Mix strategies based on tenant tier

    Subclasses should implement:
    - get_session(): Get database session for tenant
    - apply_filters(): Apply tenant filters to queries
    - initialize_tenant(): Setup storage for new tenant
    - destroy_tenant(): Cleanup tenant storage

    Example:
        ```python
        class CustomIsolationProvider(BaseIsolationProvider):
            def __init__(self, config: TenancyConfig):
                super().__init__(config)

            async def get_session(self, tenant: Tenant) -> AsyncSession:
                # Return tenant-scoped session
                ...

            async def apply_filters(self, query, tenant):
                # Apply tenant filters
                return query.where(Table.tenant_id == tenant.id)
        ```

    Attributes:
        config: Tenancy configuration
    """

    def __init__(self, config: TenancyConfig) -> None:
        """Initialize base isolation provider.

        Args:
            config: Tenancy configuration
        """
        self.config = config
        logger.debug("Initialized %s", self.__class__.__name__)

    @abstractmethod
    @asynccontextmanager
    async def get_session(self, tenant: Tenant) -> AsyncIterator[AsyncSession]:
        """Get database session scoped to tenant.

        This should return an async context manager that yields a database
        session properly configured for the tenant's data.

        Args:
            tenant: Tenant to get session for

        Yields:
            Database session scoped to tenant

        Raises:
            IsolationError: If session cannot be created

        Example:
            ```python
            async with provider.get_session(tenant) as session:
                result = await session.execute(query)
                await session.commit()
            ```
        """
        yield  # type: ignore

    @abstractmethod
    async def apply_filters(self, query: Any, tenant: Tenant) -> Any:
        """Apply tenant filters to query.

        This modifies the query to ensure only the tenant's data is accessed.
        The specific implementation depends on the isolation strategy.

        Args:
            query: SQLAlchemy query to filter
            tenant: Current tenant

        Returns:
            Filtered query

        Example:
            ```python
            query = select(User)
            filtered = await provider.apply_filters(query, tenant)
            # filtered query now only returns tenant's users
            ```
        """
        pass

    @abstractmethod
    async def initialize_tenant(self, tenant: Tenant) -> None:
        """Initialize storage for new tenant.

        This sets up the necessary database structures for a new tenant.
        Implementation depends on isolation strategy:
        - Schema: Create new schema
        - Database: Create new database
        - RLS: Create RLS policies
        - Hybrid: Depends on tenant tier

        Args:
            tenant: Tenant to initialize

        Raises:
            IsolationError: If initialization fails

        Example:
            ```python
            # Create new tenant
            tenant = await store.create(Tenant(...))

            # Initialize storage
            await provider.initialize_tenant(tenant)
            ```
        """
        pass

    @abstractmethod
    async def destroy_tenant(self, tenant: Tenant) -> None:
        """Destroy tenant storage.

        This removes all database structures for a tenant.
        WARNING: This is a destructive operation!

        Args:
            tenant: Tenant to destroy

        Raises:
            IsolationError: If destruction fails

        Example:
            ```python
            # Mark tenant as deleted
            await store.delete(tenant.id)

            # Cleanup storage
            await provider.destroy_tenant(tenant)
            ```
        """
        pass

    async def verify_isolation(self, tenant: Tenant) -> bool:
        """Verify tenant isolation is working correctly.

        This is a safety check to ensure tenant data is properly isolated.
        Should be called during initialization or health checks.

        Args:
            tenant: Tenant to verify

        Returns:
            True if isolation is correct

        Example:
            ```python
            is_isolated = await provider.verify_isolation(tenant)
            if not is_isolated:
                logger.error("Tenant isolation verification failed!")
            ```
        """
        # Default implementation - subclasses can override
        logger.warning(
            "%s does not implement verify_isolation()",
            self.__class__.__name__,
        )
        return True

    def get_schema_name(self, tenant: Tenant) -> str:
        """Get schema name for tenant.

        Helper method for schema-based isolation.

        Args:
            tenant: Tenant

        Returns:
            Schema name (e.g., 'tenant_acme_corp')
        """
        return self.config.get_schema_name(tenant.id)

    def get_database_url(self, tenant: Tenant) -> str:
        """Get database URL for tenant.

        Helper method for database-based isolation.

        Args:
            tenant: Tenant

        Returns:
            Database URL for tenant
        """
        if tenant.database_url:
            return tenant.database_url
        return self.config.get_database_url_for_tenant(tenant.id)


__all__ = ["BaseIsolationProvider"]
