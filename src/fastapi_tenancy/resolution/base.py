"""Base tenant resolver implementation.

This module provides the base class and utilities for tenant resolution strategies.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from fastapi_tenancy.core.exceptions import TenantNotFoundError

if TYPE_CHECKING:
    from fastapi_tenancy.core.types import Tenant
    from fastapi_tenancy.storage.tenant_store import TenantStore

logger = logging.getLogger(__name__)


class BaseTenantResolver(ABC):
    """Abstract base class for tenant resolution strategies.

    All tenant resolvers must inherit from this class and implement
    the resolve() method. This provides a consistent interface for
    different resolution strategies.

    The base class also provides common functionality:
    - Tenant lookup from storage
    - Logging and error handling
    - Caching support (optional)

    Subclasses should implement:
    - resolve(): Extract tenant identifier from request
    - get_tenant_by_identifier(): Lookup tenant (can override for custom logic)

    Example:
        ```python
        class CustomResolver(BaseTenantResolver):
            def __init__(self, tenant_store: TenantStore):
                super().__init__(tenant_store)

            async def resolve(self, request: Request) -> Tenant:
                # Extract tenant ID from custom header
                tenant_id = request.headers.get("X-Custom-Tenant")
                if not tenant_id:
                    raise TenantResolutionError("Missing custom header")

                # Lookup tenant
                return await self.get_tenant_by_identifier(tenant_id)
        ```

    Attributes:
        tenant_store: Storage backend for tenant lookup
    """

    def __init__(self, tenant_store: TenantStore | None = None) -> None:
        """Initialize base resolver.

        Args:
            tenant_store: Optional tenant storage backend for lookups
        """
        self.tenant_store = tenant_store
        logger.debug("Initialized %s", self.__class__.__name__)

    @abstractmethod
    async def resolve(self, request: Any) -> Tenant:
        """Resolve tenant from request.

        This is the main method that must be implemented by all resolvers.
        It should extract tenant information from the request and return
        the corresponding Tenant object.

        Args:
            request: FastAPI request object

        Returns:
            Resolved tenant

        Raises:
            TenantResolutionError: If tenant cannot be resolved from request
            TenantNotFoundError: If tenant does not exist
        """
        pass

    async def get_tenant_by_identifier(self, identifier: str) -> Tenant:
        """Get tenant by identifier using storage backend.

        This is a helper method that resolvers can use to lookup tenants.
        It can be overridden for custom lookup logic or caching.

        Args:
            identifier: Tenant identifier to lookup

        Returns:
            Tenant object

        Raises:
            TenantNotFoundError: If tenant does not exist
            ValueError: If no tenant store configured

        Example:
            ```python
            async def resolve(self, request: Request) -> Tenant:
                tenant_id = extract_from_request(request)
                return await self.get_tenant_by_identifier(tenant_id)
            ```
        """
        if not self.tenant_store:
            raise ValueError(
                f"{self.__class__.__name__} requires tenant_store to be configured"
            )

        try:
            tenant = await self.tenant_store.get_by_identifier(identifier)
            logger.debug(
                "Resolved tenant %r to %s using %s",
                identifier, tenant.id, self.__class__.__name__,
            )
            return tenant
        except TenantNotFoundError:
            logger.warning(
                "Tenant not found: %r using %s",
                identifier, self.__class__.__name__,
            )
            raise

    def validate_tenant_identifier(self, identifier: str) -> bool:
        """Validate tenant identifier format.

        This provides basic validation that can be overridden by subclasses
        for strategy-specific validation.

        Args:
            identifier: Tenant identifier to validate

        Returns:
            True if valid, False otherwise

        Example:
            ```python
            if not self.validate_tenant_identifier(tenant_id):
                raise TenantResolutionError("Invalid tenant ID format")
            ```
        """
        from fastapi_tenancy.utils.validation import validate_tenant_identifier

        return validate_tenant_identifier(identifier)


__all__ = ["BaseTenantResolver"]
