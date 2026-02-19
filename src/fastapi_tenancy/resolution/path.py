"""Path-based tenant resolution strategy.

Resolves tenant from URL path (e.g., /tenants/{tenant-id}/resource).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi_tenancy.core.exceptions import TenantResolutionError
from fastapi_tenancy.resolution.base import BaseTenantResolver

if TYPE_CHECKING:
    from fastapi import Request

    from fastapi_tenancy.core.types import Tenant
    from fastapi_tenancy.storage.tenant_store import TenantStore

logger = logging.getLogger(__name__)


class PathTenantResolver(BaseTenantResolver):
    """Resolve tenant from URL path.

    This resolver extracts the tenant identifier from a specific position
    in the URL path.

    Example URLs:
        /tenants/acme-corp/users → tenant: acme-corp
        /tenants/widgets-inc/api/orders → tenant: widgets-inc
        /api/tenants/tenant-123/dashboard → tenant: tenant-123

    Attributes:
        path_prefix: URL path prefix before tenant ID
        tenant_store: Storage backend for tenant lookup
    """

    def __init__(
        self,
        path_prefix: str = "/tenants",
        tenant_store: TenantStore | None = None,
    ) -> None:
        """Initialize path-based resolver.

        Args:
            path_prefix: URL path prefix before tenant ID
            tenant_store: Optional tenant storage backend
        """
        super().__init__(tenant_store)
        self.path_prefix = path_prefix.rstrip("/")
        logger.info("Initialized PathTenantResolver with prefix=%r", self.path_prefix)

    async def resolve(self, request: Request) -> Tenant:
        """Resolve tenant from URL path."""
        path = request.url.path

        if not path.startswith(self.path_prefix):
            raise TenantResolutionError(
                reason=f"Path '{path}' does not start with prefix '{self.path_prefix}'",
                strategy="path",
                details={"path": path, "expected_prefix": self.path_prefix},
            )

        # Extract tenant ID from path
        path_parts = path[len(self.path_prefix) :].strip("/").split("/")

        if not path_parts or not path_parts[0]:
            raise TenantResolutionError(
                reason="No tenant ID found in path",
                strategy="path",
                details={"path": path},
            )

        tenant_id = path_parts[0]

        if not self.validate_tenant_identifier(tenant_id):
            raise TenantResolutionError(
                reason=f"Invalid tenant identifier format in path: '{tenant_id}'",
                strategy="path",
                details={"path": path, "tenant_id": tenant_id},
            )

        return await self.get_tenant_by_identifier(tenant_id)


__all__ = ["PathTenantResolver"]
