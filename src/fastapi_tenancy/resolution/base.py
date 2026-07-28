"""Abstract base class for tenant resolution strategies.

:class:`BaseTenantResolver` is an optional convenience base class.  The only
**required** contract is the :class:`~fastapi_tenancy.core.types.TenantResolver`
structural protocol — any object with an ``async def resolve(request)`` method
satisfies it, whether or not it inherits from this class.

Note: ``TenantResolver`` is a ``@runtime_checkable`` Protocol, not an ABC.
Protocols do not support ``.register()``.  Duck-typing is automatic: any class
with an ``async def resolve(request)`` method satisfies the protocol via
``isinstance(obj, TenantResolver)`` without registration.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.requests import Request

    from fastapi_tenancy.core.types import Tenant
    from fastapi_tenancy.storage.tenant_store import TenantStore

logger = logging.getLogger(__name__)


class BaseTenantResolver(ABC):
    """Optional abstract base class for tenant resolution strategies.

    Subclass this to build a custom resolution strategy::

        class CookieResolver(BaseTenantResolver):
            async def resolve(self, request: Request) -> Tenant:
                tenant_id = request.cookies.get("X-Tenant")
                if not tenant_id:
                    raise TenantResolutionError("Cookie missing", strategy="cookie")
                return await self.store.get_by_identifier(tenant_id)

    Alternatively, implement the ``TenantResolver`` protocol directly —
    duck-typing is sufficient (no inheritance required).

    Args:
        store: The tenant metadata store used to look up tenants.
    """

    def __init__(self, store: TenantStore[Tenant]) -> None:
        self.store = store

    @abstractmethod
    async def resolve(self, request: Request) -> Tenant:
        """Resolve the current tenant from *request*.

        .. important:: Anti-enumeration contract

            Every failure mode — missing identifier, malformed identifier, and
            **unknown tenant** — should raise ``TenantResolutionError`` with the
            same generic reason.  All four built-in resolvers do this: they
            catch ``TenantNotFoundError`` from the store and re-raise it as a
            resolution error, so the middleware answers 400 rather than 404.

            Letting ``TenantNotFoundError`` propagate produces a 404, which
            tells an attacker the identifier *format* was valid and turns the
            endpoint into a tenant-enumeration oracle.  Only do that if your
            tenant identifiers are already public and enumeration is
            acceptable for your deployment.

            Errors describing the *credential* rather than the tenant (an
            expired or badly signed token) may keep specific reasons — the
            caller already holds the credential.

        Args:
            request: Incoming FastAPI / Starlette request.

        Returns:
            The resolved :class:`~fastapi_tenancy.core.types.Tenant`.

        Raises:
            TenantResolutionError: On every failure mode, including an unknown
                tenant — see the anti-enumeration note above.
        """


__all__ = ["BaseTenantResolver"]
