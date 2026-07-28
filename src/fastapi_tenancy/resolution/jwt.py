"""JWT-based tenant resolution strategy.

Decodes a Bearer JSON Web Token from the ``Authorization`` header and reads a
configured claim to identify the tenant.

Dependencies
------------
Requires the ``PyJWT`` package — install via the ``[jwt]`` extra::

    pip install fastapi-tenancy[jwt]

Supported algorithms
--------------------
All algorithms supported by ``PyJWT`` are available.  The default is
``HS256``.  For RS256 (asymmetric), pass ``secret`` as the PEM-encoded
public key.

Security
--------
- The token signature is always verified; do not disable this.
- Token expiry (``exp`` claim) is verified automatically by PyJWT.
- The extracted tenant identifier is validated against slug rules before
  any database lookup.
- The ``secret`` parameter is **never** included in error messages or logs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi_tenancy.core.exceptions import TenantNotFoundError, TenantResolutionError
from fastapi_tenancy.resolution.base import BaseTenantResolver
from fastapi_tenancy.utils.validation import validate_tenant_identifier

if TYPE_CHECKING:
    from starlette.requests import Request

    from fastapi_tenancy.core.types import Tenant
    from fastapi_tenancy.storage.tenant_store import TenantStore

logger = logging.getLogger(__name__)

_BEARER_PREFIX = "Bearer "
_GENERIC_REASON = "Tenant not found"


class JWTTenantResolver(BaseTenantResolver):
    """Resolve the current tenant from a signed Bearer JWT.

    Reads ``Authorization: Bearer <token>`` from the request, verifies the
    signature, and extracts the configured claim (default: ``tenant_id``).

    Args:
        store: Tenant metadata store.
        secret: JWT signing secret (HMAC) or public key (RSA).  Required.
        algorithm: Signing algorithm (default: ``"HS256"``).
        tenant_claim: JWT payload claim holding the tenant identifier
            (default: ``"tenant_id"``).
        audience: Expected ``aud`` claim value.  When set, PyJWT verifies that
            the decoded token contains a matching audience claim and raises
            ``TenantResolutionError`` otherwise.  Strongly recommended when
            the same JWT secret is shared across multiple services to prevent
            cross-service token replay attacks.  Default: ``None`` (no audience
            check — a warning is emitted at resolver construction time).

    Raises:
        ImportError: When ``PyJWT`` is not installed.

    Example::

        resolver = JWTTenantResolver(
            store,
            secret="my-super-secret-key-at-least-32-chars",
            tenant_claim="tenant_id",
            audience="my-api-service",
        )

        # Request: Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6Ikp...
        tenant = await resolver.resolve(request)
    """

    def __init__(
        self,
        store: TenantStore[Tenant],
        secret: str,
        algorithm: str | list[str] = "HS256",
        tenant_claim: str = "tenant_id",
        audience: str | None = None,
    ) -> None:
        super().__init__(store)

        # Normalise to a list so we can support rotation (e.g. RS256 → ES256
        # migration with both keys valid for the transition window).  A
        # single-string value remains backward compatible; PyJWT.decode
        # accepts ``algorithms=[...]`` natively.
        algorithms = [algorithm] if isinstance(algorithm, str) else list(algorithm)
        if not algorithms:
            msg = "JWTTenantResolver: algorithm must be a non-empty string or list."
            raise ValueError(msg)

        # Defence in depth against the JWT alg=none attack.  PyJWT will
        # already refuse to decode an unsigned token when ``"none"`` is
        # not in ``algorithms=[...]`` — but if the resolver itself is
        # configured with ``algorithm="none"`` (typo, mis-merged config,
        # copy-paste from a JWT debugger, *or* a rotation list that
        # accidentally includes "none"), every request would be accepted
        # without signature verification.  Reject every offending element
        # at construction time so the failure is loud and at startup.
        for alg in algorithms:
            if not alg or alg.strip().lower() == "none":
                msg = (
                    f"JWTTenantResolver: algorithm={alg!r} is not permitted.  "
                    "Unsigned JWTs would let any caller forge tenant identity.  "
                    "Use a real signing algorithm (default: 'HS256')."
                )
                raise ValueError(msg)

        try:
            import jwt as _pyjwt  # noqa: PLC0415

            self._jwt = _pyjwt
        except ImportError as exc:
            raise ImportError(
                "JWT resolution requires 'PyJWT>=2.8'. "
                "Install it with: pip install 'fastapi-tenancy[jwt]'"
            ) from exc

        self._secret = secret
        # ``self._algorithm`` retains the first element for backward-compatible
        # introspection (some test seams read it).  ``self._algorithms`` is
        # the list actually passed to PyJWT.
        self._algorithm = algorithms[0]
        self._algorithms = algorithms
        self._tenant_claim = tenant_claim
        self._audience = audience

        # Warn when no audience is configured so operators are
        # alerted to the cross-service replay risk during startup.
        if audience is None:
            logger.warning(
                "JWTTenantResolver: no 'audience' configured.  If multiple "
                "services share the same JWT secret, set audience= to prevent "
                "cross-service token replay attacks."
            )

    def _decode_token(self, token: str | bytes) -> dict[str, Any]:
        """Verify and decode a JWT string.

        Args:
            token: Raw JWT string (without the ``Bearer `` prefix).

        Returns:
            Decoded payload dictionary.

        Raises:
            TenantResolutionError: On any JWT verification failure including
                audience mismatch (when ``audience`` is configured).
        """
        try:
            # Pass audience so PyJWT validates the ``aud`` claim.
            # When self._audience is None the kwarg is omitted entirely so
            # behaviour is identical to the pre-fix state for callers
            # that have not configured an audience yet.
            decode_kwargs: dict[str, Any] = {
                "algorithms": self._algorithms,
            }
            if self._audience is not None:
                decode_kwargs["audience"] = self._audience

            return self._jwt.decode(token, self._secret, **decode_kwargs)
        except self._jwt.ExpiredSignatureError:
            raise TenantResolutionError(
                reason="JWT token has expired",
                strategy="jwt",
            ) from None
        except self._jwt.InvalidAudienceError:
            raise TenantResolutionError(
                reason="JWT audience claim does not match expected audience",
                strategy="jwt",
                details={"expected_audience": self._audience},
            ) from None
        except self._jwt.InvalidTokenError as exc:
            raise TenantResolutionError(
                reason="JWT token is invalid or signature verification failed",
                strategy="jwt",
                details={"jwt_error": type(exc).__name__},
            ) from exc

    async def resolve(self, request: Request) -> Tenant:
        """Decode the Bearer JWT and resolve the tenant from the payload claim.

        Args:
            request: Incoming HTTP request.

        Returns:
            Resolved :class:`~fastapi_tenancy.core.types.Tenant`.

        Raises:
            TenantResolutionError: On any failure — missing/malformed
                Authorization header, invalid/expired JWT, audience mismatch,
                missing/invalid tenant claim, or unknown tenant.  The
                identifier-lookup failure is folded into the generic reason
                shared with missing/invalid identifiers so callers cannot
                enumerate tenant slugs by status-code or message comparison
                (anti-enumeration invariant).  Token-validity errors
                (expiry, signature, audience) keep their specific reasons
                because they describe the *token*, not the *tenant*.
        """
        auth_header = request.headers.get("authorization", "")
        if not auth_header:
            raise TenantResolutionError(
                reason="Authorization header is missing",
                strategy="jwt",
            )
        if not auth_header.startswith(_BEARER_PREFIX):
            raise TenantResolutionError(
                reason="Authorization header does not use Bearer scheme",
                strategy="jwt",
            )

        token = auth_header[len(_BEARER_PREFIX) :].strip()
        if not token:
            raise TenantResolutionError(
                reason="Bearer token is empty",
                strategy="jwt",
            )

        payload = self._decode_token(token)

        identifier = payload.get(self._tenant_claim)
        if not identifier or not isinstance(identifier, str):
            raise TenantResolutionError(
                reason=_GENERIC_REASON,
                strategy="jwt",
                details={"claim": self._tenant_claim},
            )
        if not validate_tenant_identifier(identifier):
            raise TenantResolutionError(
                reason=_GENERIC_REASON,
                strategy="jwt",
                details={"claim": self._tenant_claim},
            )

        logger.debug("JWT resolver: claim=%r → identifier=%r", self._tenant_claim, identifier)
        try:
            return await self.store.get_by_identifier(identifier)
        except TenantNotFoundError:
            # Re-raise as TenantResolutionError with the same generic message
            # so unknown-tenant returns the same status as missing/invalid
            # claim — a 404 would confirm to the holder of a forged token
            # that the claim format was valid.
            raise TenantResolutionError(  # noqa: B904
                reason=_GENERIC_REASON,
                strategy="jwt",
                details={"claim": self._tenant_claim},
            )


__all__ = ["JWTTenantResolver"]
