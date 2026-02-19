"""Header-based tenant resolution strategy.

Resolves tenant from HTTP header (default: X-Tenant-ID).
This is the most common and straightforward resolution strategy.
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


class HeaderTenantResolver(BaseTenantResolver):
    """Resolve tenant from HTTP header.

    Extracts the tenant identifier from a specified HTTP header and looks up
    the corresponding tenant in the store.  This is the simplest and most
    common resolution strategy.

    Advantages
    ----------
    * Simple to implement and test.
    * Works with any HTTP client.
    * No URL routing changes needed.
    * Explicit tenant selection.

    Use cases
    ---------
    * API clients and SDKs.
    * Mobile applications.
    * Microservice-to-microservice communication.
    * Development and CI environments.

    Example request::

        GET /api/users HTTP/1.1
        Host: api.example.com
        X-Tenant-ID: acme-corp
        Authorization: Bearer token123

    Example usage::

        resolver = HeaderTenantResolver(
            header_name="X-Tenant-ID",
            tenant_store=store,
        )
        tenant = await resolver.resolve(request)

    Parameters
    ----------
    header_name:
        Name of the HTTP header that carries the tenant identifier.
    tenant_store:
        Storage backend for tenant lookup.
    case_sensitive:
        When ``True``, the header name must match exactly.
        When ``False`` (default), matching is case-insensitive —
        standard HTTP/1.1 behaviour.
    """

    def __init__(
        self,
        header_name: str = "X-Tenant-ID",
        tenant_store: TenantStore | None = None,
        case_sensitive: bool = False,
    ) -> None:
        super().__init__(tenant_store)
        self.header_name = header_name
        self.case_sensitive = case_sensitive
        logger.info(
            "HeaderTenantResolver header=%r case_sensitive=%s",
            header_name,
            case_sensitive,
        )

    async def resolve(self, request: Request) -> Tenant:
        """Resolve tenant from HTTP header.

        Parameters
        ----------
        request:
            Incoming FastAPI / Starlette request.

        Returns
        -------
        Tenant
            The resolved tenant.

        Raises
        ------
        TenantResolutionError
            If the header is absent, empty, or the identifier has an
            invalid format.
        TenantNotFoundError
            If no tenant exists with the extracted identifier.
        """
        tenant_id = self._get_header_value(request)

        if not tenant_id:
            logger.warning("Header %r not found in request", self.header_name)
            # Security: do NOT expose full header list
            # Leaking all header names in an error response can reveal
            # internal infrastructure headers (X-Forwarded-For, auth tokens,
            # etc.) to untrusted clients.  Only expose the name that was
            # expected — never the full header map.
            raise TenantResolutionError(
                reason="Required tenant header not found in request",
                strategy="header",
                details={"expected_header": self.header_name},
            )

        tenant_id = tenant_id.strip()

        if not tenant_id:
            logger.warning("Header %r is empty", self.header_name)
            raise TenantResolutionError(
                reason="Tenant header is present but empty",
                strategy="header",
                details={"header_name": self.header_name},
            )

        if not self.validate_tenant_identifier(tenant_id):
            logger.warning("Invalid tenant identifier format: %r", tenant_id)
            raise TenantResolutionError(
                reason="Invalid tenant identifier format",
                strategy="header",
                details={
                    "hint": (
                        "Identifier must be 3-63 characters, start with a letter, "
                        "and contain only lowercase letters, digits, and hyphens."
                    ),
                },
            )

        logger.debug("Resolving tenant from header %r: %r", self.header_name, tenant_id)
        return await self.get_tenant_by_identifier(tenant_id)

    def _get_header_value(self, request: Request) -> str | None:
        """Extract the header value from the request.

        Handles both case-sensitive and case-insensitive matching.
        HTTP headers are case-insensitive by spec (RFC 7230 §3.2); the
        default ``case_sensitive=False`` honours that.
        """
        if self.case_sensitive:
            return request.headers.get(self.header_name)

        header_lower = self.header_name.lower()
        for key, value in request.headers.items():
            if key.lower() == header_lower:
                return value
        return None


__all__ = ["HeaderTenantResolver"]
