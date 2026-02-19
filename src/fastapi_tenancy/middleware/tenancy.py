"""Tenancy middleware — request-scoped tenant resolution and context management.

Changes from v0.1.0
-------------------
- ``should_skip`` (public, path-only) and ``_should_skip`` (private,
  OPTIONS + path) were inconsistent: tests calling the public helper
  missed the OPTIONS check.  Consolidated into a single private method
  ``_should_skip_request(request)`` and a public test-friendly helper
  ``_is_path_skipped(path)`` with clear separation of concerns.
- All f-string log calls replaced with ``%s`` deferred formatting.
- ``debug_headers`` flag now also gates internal detail exposure in
  ``TenancyError`` responses (previously gated on ``database_echo``).
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from fastapi_tenancy.core.context import TenantContext
from fastapi_tenancy.core.exceptions import (
    TenancyError,
    TenantInactiveError,
    TenantNotFoundError,
    TenantResolutionError,
)

if TYPE_CHECKING:
    from starlette.middleware.base import RequestResponseEndpoint

    from fastapi_tenancy.core.config import TenancyConfig
    from fastapi_tenancy.manager import TenancyManager
    from fastapi_tenancy.resolution.base import BaseTenantResolver

logger = logging.getLogger(__name__)

_DEFAULT_SKIP_PATHS: list[str] = [
    "/health",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
]


class TenancyMiddleware(BaseHTTPMiddleware):
    """Resolve tenant for every request and set async-safe context.

    Processing pipeline
    -------------------
    1. Skip resolution for public paths and OPTIONS requests (preflight).
    2. Resolve tenant via the configured strategy.
    3. Validate tenant is active.
    4. Set :class:`~fastapi_tenancy.core.context.TenantContext`.
    5. Forward to the next handler.
    6. Clear context in ``finally`` — **always** runs, even on exception.

    Registration
    ------------
    Use :meth:`TenancyManager.create_lifespan` (recommended)::

        app = FastAPI(lifespan=TenancyManager.create_lifespan(config))

    Or manually — middleware MUST be registered before the lifespan yields::

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            app.add_middleware(
                TenancyMiddleware,
                config=config,
                manager=manager,
            )
            await manager.initialize()
            yield
            await manager.shutdown()

    .. warning::
        Calling ``app.add_middleware`` after ``yield`` (i.e. after the
        application has started) raises ``RuntimeError`` because Starlette
        has already built and frozen the middleware stack.

    Parameters
    ----------
    app:
        The ASGI application (injected by Starlette's middleware machinery).
    config:
        Tenancy configuration — used for ``debug_headers`` decisions.
    resolver:
        Pre-built resolver.  Mutually exclusive with ``manager``.
    skip_paths:
        URL prefixes that bypass tenant resolution.
    debug_headers:
        When ``True`` adds ``X-Tenant-ID`` / ``X-Tenant-Identifier`` to
        every response and includes error details in 5xx responses.
    manager:
        If provided, the resolver is fetched via ``manager.resolver`` after
        ``manager.initialize()`` completes, allowing the middleware to be
        registered *before* ``initialize()`` is called.
    """

    def __init__(
        self,
        app: Any,
        *,
        config: TenancyConfig | None = None,
        resolver: BaseTenantResolver | None = None,
        skip_paths: list[str] | None = None,
        debug_headers: bool = False,
        manager: TenancyManager | None = None,
    ) -> None:
        super().__init__(app)
        self.config = config
        self._resolver = resolver
        self._manager = manager
        self.skip_paths: list[str] = (
            skip_paths if skip_paths is not None else list(_DEFAULT_SKIP_PATHS)
        )
        self.debug_headers = debug_headers
        logger.info("TenancyMiddleware registered skip_paths=%s", self.skip_paths)

    @property
    def resolver(self) -> BaseTenantResolver | None:
        """Return the active resolver.

        If a :class:`~fastapi_tenancy.manager.TenancyManager` was injected,
        delegate to its ``resolver`` attribute so the middleware always uses
        the post-initialisation resolver even if it was registered before
        ``initialize()`` ran.
        """
        if self._manager is not None and hasattr(self._manager, "resolver"):
            return self._manager.resolver
        return self._resolver

    def _is_path_skipped(self, path: str) -> bool:
        """Return ``True`` if *path* prefix is in the skip list.

        This is the low-level, path-only check exposed for testing::

            assert middleware._is_path_skipped("/health")       # True
            assert not middleware._is_path_skipped("/api/data") # False
        """
        return any(path.startswith(p) for p in self.skip_paths)

    def _should_skip_request(self, request: Request) -> bool:
        """Return ``True`` if this request should bypass tenant resolution.

        Skips:
        * ``OPTIONS`` requests (CORS preflight).
        * Any path whose prefix is in :attr:`skip_paths`.
        """
        if request.method == "OPTIONS":
            return True
        return self._is_path_skipped(request.url.path)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.perf_counter()

        if self._should_skip_request(request):
            logger.debug("Skipping tenant resolution for %s", request.url.path)
            return await call_next(request)

        # Guard: resolver must be set before serving any tenant-aware request.
        # This is set during app startup via TenancyManager.create_lifespan().
        # Checked outside the try/except so it is never accidentally swallowed.
        _resolver = self.resolver
        if _resolver is None:
            logger.error(
                "TenancyMiddleware has no resolver — was TenancyManager.create_lifespan() used?"
            )
            return self._error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "service_unavailable",
                "Tenant service is not yet initialised.",
                {},
            )

        try:
            tenant = await _resolver.resolve(request)

            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "Resolved tenant %s (id=%s) in %.2f ms",
                tenant.identifier,
                tenant.id,
                elapsed_ms,
            )

            if not tenant.is_active():
                logger.warning(
                    "Inactive tenant access: %s status=%s",
                    tenant.identifier,
                    tenant.status.value,
                )
                raise TenantInactiveError(
                    tenant_id=tenant.id,
                    status=tenant.status.value,
                    details={
                        "identifier": tenant.identifier,
                        "message": (
                            f"Tenant {tenant.identifier!r} is {tenant.status.value}"
                        ),
                    },
                )

            TenantContext.set(tenant)
            request.state.tenant = tenant
            TenantContext.set_metadata("request_path", request.url.path)
            TenantContext.set_metadata("request_method", request.method)

            response = await call_next(request)

            if self.debug_headers:
                response.headers["X-Tenant-ID"] = tenant.id
                response.headers["X-Tenant-Identifier"] = tenant.identifier

            total_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "Request tenant=%s %s %s [%d] %.2f ms",
                tenant.identifier,
                request.method,
                request.url.path,
                response.status_code,
                total_ms,
            )
            return response

        except TenantNotFoundError as exc:
            logger.error("Tenant not found: %s", exc.message)
            return self._error_response(
                status.HTTP_404_NOT_FOUND,
                "tenant_not_found",
                str(exc),
                exc.details,
            )

        except TenantResolutionError as exc:
            logger.error("Tenant resolution failed: %s", exc.message)
            return self._error_response(
                status.HTTP_400_BAD_REQUEST,
                "tenant_resolution_failed",
                str(exc),
                exc.details,
            )

        except TenantInactiveError as exc:
            logger.warning("Inactive tenant access: %s", exc.message)
            return self._error_response(
                status.HTTP_403_FORBIDDEN,
                "tenant_inactive",
                str(exc),
                exc.details,
            )

        except TenancyError as exc:
            logger.error("Tenancy error: %s", exc.message, exc_info=True)
            return self._error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "tenancy_error",
                "An error occurred processing tenant information",
                # Only expose internal details when debug mode is on
                exc.details if self.debug_headers else {},
            )

        except Exception as exc:
            logger.error("Unexpected middleware error: %s", exc, exc_info=True)
            return self._error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "internal_error",
                "An unexpected error occurred",
                {},
            )

        finally:
            TenantContext.clear()

    @staticmethod
    def _error_response(
        status_code: int,
        error: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> JSONResponse:
        content: dict[str, Any] = {"error": error, "message": message}
        if details:
            content["details"] = details
        return JSONResponse(status_code=status_code, content=content)


__all__ = ["TenancyMiddleware"]
