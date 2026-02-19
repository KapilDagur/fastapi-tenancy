"""JWT-based tenant resolution strategy.

Resolves tenant from JWT token claims.

Requires the ``jwt`` extra::

    pip install fastapi-tenancy[jwt]
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

try:
    from jose import JWTError, jwt
    _JOSE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _JOSE_AVAILABLE = False
    JWTError = Exception  # type: ignore[misc, assignment]
    jwt = None  # type: ignore[assignment]

from fastapi_tenancy.core.exceptions import TenantResolutionError
from fastapi_tenancy.resolution.base import BaseTenantResolver

if TYPE_CHECKING:
    from fastapi import Request

    from fastapi_tenancy.core.types import Tenant
    from fastapi_tenancy.storage.tenant_store import TenantStore

logger = logging.getLogger(__name__)


class JWTTenantResolver(BaseTenantResolver):
    """Resolve tenant from JWT token.

    This resolver extracts the tenant identifier from a claim in the JWT token.

    Example JWT payload::

        {
            "sub": "user-123",
            "tenant_id": "acme-corp",
            "exp": 1234567890
        }

    Attributes:
        secret: JWT secret key for validation
        algorithm: JWT signing algorithm
        tenant_claim: Name of claim containing tenant ID
        tenant_store: Storage backend
    """

    def __init__(
        self,
        secret: str,
        algorithm: str = "HS256",
        tenant_claim: str = "tenant_id",
        tenant_store: TenantStore | None = None,
    ) -> None:
        """Initialize JWT resolver.

        Raises:
            ImportError: If ``python-jose`` is not installed
                (``pip install fastapi-tenancy[jwt]``).
            ValueError: If ``secret`` is empty.
        """
        if not _JOSE_AVAILABLE:
            raise ImportError(
                "JWTTenantResolver requires the 'jwt' extra: "
                "pip install fastapi-tenancy[jwt]"
            )
        if not secret:
            raise ValueError(
                "JWTTenantResolver requires a non-empty secret key."
            )
        super().__init__(tenant_store)
        self.secret = secret
        self.algorithm = algorithm
        self.tenant_claim = tenant_claim
        logger.info("Initialized JWTTenantResolver with claim=%r", tenant_claim)

    async def resolve(self, request: Request) -> Tenant:
        """Resolve tenant from JWT token."""
        # Extract token from Authorization header
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            raise TenantResolutionError(
                reason="Authorization header not found",
                strategy="jwt",
            )

        # Extract Bearer token
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise TenantResolutionError(
                reason="Invalid Authorization header format. Expected: Bearer <token>",
                strategy="jwt",
            )

        token = parts[1]

        # Decode and validate JWT — log the internal reason, never expose it
        try:
            payload = jwt.decode(token, self.secret, algorithms=[self.algorithm])
        except JWTError as e:
            logger.warning("JWT validation failed: %s", e)
            raise TenantResolutionError(
                reason="JWT validation failed",
                strategy="jwt",
            ) from e

        # Extract tenant ID from claim — do not return claim names to the caller
        tenant_id = payload.get(self.tenant_claim)
        if not tenant_id:
            logger.warning(
                "JWT missing required claim %r (available claims omitted for security)",
                self.tenant_claim,
            )
            raise TenantResolutionError(
                reason="JWT token does not contain the required tenant claim",
                strategy="jwt",
            )

        if not self.validate_tenant_identifier(str(tenant_id)):
            raise TenantResolutionError(
                reason="Invalid tenant identifier format in JWT claim",
                strategy="jwt",
            )

        return await self.get_tenant_by_identifier(tenant_id)


__all__ = ["JWTTenantResolver"]
