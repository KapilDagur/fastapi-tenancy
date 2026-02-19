"""Subdomain-based tenant resolution strategy.

Resolves tenant from subdomain (e.g., tenant.example.com).
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


class SubdomainTenantResolver(BaseTenantResolver):
    """Resolve tenant from subdomain.

    This resolver extracts the tenant identifier from the subdomain portion
    of the request hostname.

    Advantages:
    - Clean, user-friendly URLs
    - Natural tenant isolation
    - SEO-friendly
    - Easy branding per tenant

    Requirements:
    - Wildcard DNS (*.example.com → your-server)
    - SSL certificate for wildcard domain
    - Proper domain configuration

    Example Requests:
        ```
        https://acme-corp.example.com/dashboard
        → Tenant: acme-corp

        https://widgets-inc.example.com/api/users
        → Tenant: widgets-inc

        https://app.tenant-123.example.com/
        → Tenant: tenant-123
        ```

    Example Usage:
        ```python
        resolver = SubdomainTenantResolver(
            domain_suffix=".example.com",
            tenant_store=store,
        )

        # Resolve from request
        tenant = await resolver.resolve(request)
        ```

    Attributes:
        domain_suffix: Main domain suffix (e.g., '.example.com')
        tenant_store: Storage backend for tenant lookup
    """

    def __init__(
        self,
        domain_suffix: str,
        tenant_store: TenantStore | None = None,
    ) -> None:
        """Initialize subdomain-based resolver.

        Args:
            domain_suffix: Main domain suffix (e.g., '.example.com')
            tenant_store: Optional tenant storage backend

        Example:
            ```python
            # Standard usage
            resolver = SubdomainTenantResolver(domain_suffix=".example.com")

            # With custom domain
            resolver = SubdomainTenantResolver(domain_suffix=".myapp.io")
            ```
        """
        super().__init__(tenant_store)

        # Ensure domain suffix starts with dot
        self.domain_suffix = domain_suffix.lower()
        if not self.domain_suffix.startswith("."):
            self.domain_suffix = f".{self.domain_suffix}"

        logger.info("Initialized SubdomainTenantResolver with suffix=%r", self.domain_suffix)

    async def resolve(self, request: Request) -> Tenant:
        """Resolve tenant from subdomain.

        Extracts subdomain from hostname and looks up the tenant.

        Args:
            request: FastAPI request object

        Returns:
            Resolved tenant

        Raises:
            TenantResolutionError: If hostname doesn't match, no subdomain, or invalid format
            TenantNotFoundError: If tenant does not exist

        Example:
            ```python
            # Request to https://acme-corp.example.com/api/users
            tenant = await resolver.resolve(request)
            # Returns: Tenant(id="123", identifier="acme-corp", ...)
            ```
        """
        # Get hostname
        host = request.url.hostname
        if not host:
            logger.warning("No hostname in request")
            raise TenantResolutionError(
                reason="No hostname in request",
                strategy="subdomain",
                details={"url": str(request.url)},
            )

        host = host.lower()
        logger.debug("Resolving tenant from hostname: %s", host)

        # Verify domain suffix matches
        if not host.endswith(self.domain_suffix):
            logger.warning(
                "Host %r does not match domain suffix %r",
                host, self.domain_suffix,
            )
            raise TenantResolutionError(
                reason=f"Host '{host}' does not match domain suffix '{self.domain_suffix}'",
                strategy="subdomain",
                details={
                    "host": host,
                    "expected_suffix": self.domain_suffix,
                    "hint": "Ensure the request is made to a subdomain of the configured domain",
                },
            )

        # Extract subdomain
        subdomain = host[: -len(self.domain_suffix)]

        if not subdomain:
            logger.warning("No subdomain found in hostname: %s", host)
            raise TenantResolutionError(
                reason="No subdomain found in hostname",
                strategy="subdomain",
                details={
                    "host": host,
                    "hint": "Request must be made to tenant.example.com, not example.com",
                },
            )

        # Handle multi-level subdomains (use first part only)
        # e.g., app.acme-corp.example.com → acme-corp
        if "." in subdomain:
            parts = subdomain.split(".")
            tenant_id = parts[-1]  # Use rightmost part before domain
            logger.debug(
                "Multi-level subdomain detected: %s using tenant_id: %s",
                subdomain, tenant_id,
            )
        else:
            tenant_id = subdomain

        # Validate format
        if not self.validate_tenant_identifier(tenant_id):
            logger.warning("Invalid tenant identifier format in subdomain: %r", tenant_id)
            raise TenantResolutionError(
                reason=f"Invalid tenant identifier format in subdomain: '{tenant_id}'",
                strategy="subdomain",
                details={
                    "subdomain": subdomain,
                    "tenant_id": tenant_id,
                    "hint": "Subdomain must contain only lowercase letters, numbers, and hyphens",
                },
            )

        # Lookup tenant
        logger.debug("Resolving tenant from subdomain: %s", tenant_id)
        return await self.get_tenant_by_identifier(tenant_id)


__all__ = ["SubdomainTenantResolver"]
