"""Subdomain-based tenant resolution strategy.

Extracts the tenant identifier from the leftmost subdomain of the incoming
``Host`` / ``X-Forwarded-Host`` header.

Example::

    Host: acme-corp.example.com → identifier: "acme-corp"
    Host: globex.myapp.io       → identifier: "globex"

Security notes
--------------
- Only the leftmost label is extracted; everything after the first ``.`` is
  the configured ``domain_suffix`` and is not used for identification.
- The extracted label is validated against tenant slug rules before lookup.
- ``X-Forwarded-Host`` is used when present (reverse-proxy environments).
  If your deployment does **not** use a trusted reverse proxy, disable
  ``X-Forwarded-Host`` reading by passing ``trust_x_forwarded=False``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi_tenancy.core.exceptions import TenantNotFoundError, TenantResolutionError
from fastapi_tenancy.resolution.base import BaseTenantResolver
from fastapi_tenancy.utils.validation import validate_tenant_identifier

if TYPE_CHECKING:
    from starlette.requests import Request

    from fastapi_tenancy.core.types import Tenant
    from fastapi_tenancy.storage.tenant_store import TenantStore

logger = logging.getLogger(__name__)

_GENERIC_REASON = "Tenant not found"


class SubdomainTenantResolver(BaseTenantResolver):
    """Resolve the current tenant from the leftmost subdomain.

    Args:
        store: Tenant metadata store.
        domain_suffix: The base domain (e.g. ``".example.com"``).  Used to
            strip the suffix before validation; if the host does not end
            with this suffix the resolver raises.
        trust_x_forwarded: Whether to read ``X-Forwarded-Host`` before
            ``Host``.  Default ``True``.

            .. danger:: Set this to ``False`` unless you are behind a proxy

                ``X-Forwarded-Host`` is forgeable by any client unless a
                trusted reverse proxy strips and overrides it.  With no proxy
                — or a proxy that passes the client-supplied header through —
                an attacker spoofs tenant identity outright by sending
                ``X-Forwarded-Host: victim.example.com``, and every request
                is then resolved as that tenant.

                The default remains ``True`` for backward compatibility, and a
                ``WARNING`` is logged at construction so the exposure is
                visible.  Leave it on only when *all* incoming traffic flows
                through a reverse proxy you control that sets the header
                itself (nginx, AWS ALB, Cloudflare, Envoy with
                ``HostRewrite``).  A future major release will flip the
                default to ``False``.

    Example::

        # Directly exposed, or proxy trust not established — pass False.
        resolver = SubdomainTenantResolver(
            store,
            domain_suffix=".example.com",
            trust_x_forwarded=False,
        )

        # Request: Host: acme-corp.example.com
        tenant = await resolver.resolve(request)
        # → Tenant(identifier="acme-corp", …)
    """

    def __init__(
        self,
        store: TenantStore[Tenant],
        domain_suffix: str = "",
        trust_x_forwarded: bool = True,
    ) -> None:
        super().__init__(store)
        # Normalise: always starts with "." unless empty.
        self._domain_suffix = (
            domain_suffix
            if not domain_suffix or domain_suffix.startswith(".")
            else f".{domain_suffix}"
        )
        self._trust_x_forwarded = trust_x_forwarded

        # Mirrors the JWTTenantResolver no-audience warning: the insecure-by
        # -default state stays reachable for backward compatibility, but it
        # must be visible in logs rather than silent.  Flipping the default
        # would break every deployment behind a proxy, so the warning is the
        # non-breaking half of the mitigation.
        if trust_x_forwarded:
            logger.warning(
                "SubdomainTenantResolver: trust_x_forwarded=True.  "
                "X-Forwarded-Host will be read before Host.  This is safe only "
                "if every request passes through a trusted reverse proxy that "
                "sets the header itself — otherwise an attacker can spoof "
                "tenant identity by sending the header directly."
            )

    def _extract_identifier(self, host: str) -> str:
        """Extract and validate the tenant subdomain from *host*.

        Args:
            host: Raw ``Host`` header value (may include port).

        Returns:
            The tenant identifier string.

        Raises:
            TenantResolutionError: When the subdomain cannot be extracted or
                fails validation.  Uses the generic reason to satisfy the
                anti-enumeration invariant.
        """
        # Strip port suffix (e.g. "host:8000" → "host").
        hostname = host.split(":", maxsplit=1)[0].lower().strip()

        if self._domain_suffix and not hostname.endswith(self._domain_suffix):
            raise TenantResolutionError(reason=_GENERIC_REASON, strategy="subdomain")

        parts = hostname.split(".")
        if len(parts) < 2:
            raise TenantResolutionError(reason=_GENERIC_REASON, strategy="subdomain")

        identifier = parts[0]
        if not validate_tenant_identifier(identifier):
            raise TenantResolutionError(reason=_GENERIC_REASON, strategy="subdomain")
        return identifier

    async def resolve(self, request: Request) -> Tenant:
        """Extract the tenant identifier from the request's hostname.

        Args:
            request: Incoming HTTP request.

        Returns:
            Resolved :class:`~fastapi_tenancy.core.types.Tenant`.

        Raises:
            TenantResolutionError: On any failure — missing Host header,
                missing/invalid subdomain, or unknown tenant.  All failure
                modes share the same generic reason so callers cannot
                enumerate valid tenant identifiers (anti-enumeration invariant).
        """
        host = ""
        if self._trust_x_forwarded:
            host = request.headers.get("x-forwarded-host", "")
        if not host:
            host = request.headers.get("host", "")
        if not host:
            raise TenantResolutionError(reason=_GENERIC_REASON, strategy="subdomain")

        identifier = self._extract_identifier(host)
        logger.debug("Subdomain resolver: host=%r → identifier=%r", host, identifier)
        try:
            return await self.store.get_by_identifier(identifier)
        except TenantNotFoundError:
            # Re-raise as TenantResolutionError so the middleware returns the
            # same status as missing/invalid identifiers — a 404 would confirm
            # to callers that the subdomain pointed at a real tenant slug.
            raise TenantResolutionError(reason=_GENERIC_REASON, strategy="subdomain")  # noqa: B904


__all__ = ["SubdomainTenantResolver"]
