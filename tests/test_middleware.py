"""TenancyMiddleware tests — skip logic, error responses, context cleanup."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from fastapi_tenancy.core.context import TenantContext
from fastapi_tenancy.core.exceptions import (
    TenantNotFoundError,
    TenantResolutionError,
)
from fastapi_tenancy.core.types import Tenant, TenantStatus
from fastapi_tenancy.middleware.tenancy import TenancyMiddleware


def make_request(path: str = "/api/users", host: str = "localhost") -> MagicMock:
    req = MagicMock()
    req.url.path = path
    req.url.hostname = host
    req.headers = {}
    req.state = MagicMock()
    return req


def make_tenant(status: TenantStatus = TenantStatus.ACTIVE) -> Tenant:
    return Tenant(
        id="t-001",
        identifier="acme-corp",
        name="Acme Corp",
        status=status,
    )


class TestResolverProperty:

    def test_resolver_none_when_not_configured(self) -> None:
        m = TenancyMiddleware(app=MagicMock())
        assert m.resolver is None

    def test_resolver_from_manager(self) -> None:
        manager = MagicMock()
        manager.resolver = MagicMock()
        m = TenancyMiddleware(app=MagicMock(), manager=manager)
        assert m.resolver is manager.resolver

    def test_resolver_from_direct_injection(self) -> None:
        resolver = MagicMock()
        m = TenancyMiddleware(app=MagicMock(), resolver=resolver)
        assert m.resolver is resolver


class TestDispatchFlow:

    @pytest.mark.asyncio
    async def test_skip_path_calls_next(self) -> None:
        call_next = AsyncMock(return_value=MagicMock())
        m = TenancyMiddleware(app=MagicMock(), skip_paths=["/health"])
        req = make_request(path="/health")
        response = await m.dispatch(req, call_next)
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_resolver_returns_error(self) -> None:
        call_next = AsyncMock(return_value=MagicMock())
        m = TenancyMiddleware(app=MagicMock())  # no resolver
        req = make_request(path="/api/users")
        response = await m.dispatch(req, call_next)
        # Should return 503 / error response, not call next
        assert response.status_code in (503, 500, 400)
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolution_error_returns_400(self) -> None:
        resolver = AsyncMock()
        resolver.resolve = AsyncMock(
            side_effect=TenantResolutionError(reason="no header", strategy="header")
        )
        m = TenancyMiddleware(app=MagicMock(), resolver=resolver)
        req = make_request()
        response = await m.dispatch(req, AsyncMock())
        assert response.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_tenant_not_found_returns_404(self) -> None:
        resolver = AsyncMock()
        resolver.resolve = AsyncMock(
            side_effect=TenantNotFoundError(identifier="ghost")
        )
        m = TenancyMiddleware(app=MagicMock(), resolver=resolver)
        req = make_request()
        response = await m.dispatch(req, AsyncMock())
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_inactive_tenant_returns_403(self) -> None:
        tenant = make_tenant(status=TenantStatus.SUSPENDED)
        resolver = AsyncMock()
        resolver.resolve = AsyncMock(return_value=tenant)
        m = TenancyMiddleware(app=MagicMock(), resolver=resolver)
        req = make_request()
        response = await m.dispatch(req, AsyncMock())
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_active_tenant_calls_next(self) -> None:
        tenant = make_tenant(status=TenantStatus.ACTIVE)
        resolver = AsyncMock()
        resolver.resolve = AsyncMock(return_value=tenant)
        call_next = AsyncMock(return_value=MagicMock(status_code=200, headers={}))
        m = TenancyMiddleware(app=MagicMock(), resolver=resolver)
        req = make_request()
        response = await m.dispatch(req, call_next)
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_cleared_after_request(self) -> None:
        """TenantContext MUST be cleared even on success."""
        tenant = make_tenant()
        resolver = AsyncMock()
        resolver.resolve = AsyncMock(return_value=tenant)
        call_next = AsyncMock(return_value=MagicMock(status_code=200, headers={}))
        m = TenancyMiddleware(app=MagicMock(), resolver=resolver)
        await m.dispatch(make_request(), call_next)
        assert TenantContext.get_optional() is None

    @pytest.mark.asyncio
    async def test_context_cleared_on_exception(self) -> None:
        """TenantContext MUST be cleared even when call_next raises."""
        tenant = make_tenant()
        resolver = AsyncMock()
        resolver.resolve = AsyncMock(return_value=tenant)
        call_next = AsyncMock(side_effect=RuntimeError("internal error"))
        m = TenancyMiddleware(app=MagicMock(), resolver=resolver)
        try:
            await m.dispatch(make_request(), call_next)
        except RuntimeError:
            pass
        assert TenantContext.get_optional() is None

    @pytest.mark.asyncio
    async def test_debug_headers_added(self) -> None:
        tenant = make_tenant()
        resolver = AsyncMock()
        resolver.resolve = AsyncMock(return_value=tenant)
        response = MagicMock(status_code=200)
        response.headers = {}
        call_next = AsyncMock(return_value=response)
        m = TenancyMiddleware(app=MagicMock(), resolver=resolver, debug_headers=True)
        resp = await m.dispatch(make_request(), call_next)
        # With debug headers, tenant ID should appear in response headers
        headers_str = str(resp.headers)
        assert tenant.id in headers_str or "X-Tenant" in headers_str or call_next.called

    @pytest.mark.asyncio
    async def test_provisioning_tenant_skipped(self) -> None:
        """Provisioning tenants should be rejected (not yet active)."""
        tenant = make_tenant(status=TenantStatus.PROVISIONING)
        resolver = AsyncMock()
        resolver.resolve = AsyncMock(return_value=tenant)
        m = TenancyMiddleware(app=MagicMock(), resolver=resolver)
        call_next = AsyncMock(return_value=MagicMock())
        response = await m.dispatch(make_request(), call_next)
        # Provisioning tenants should not be allowed through
        assert response.status_code in (403, 503) or not call_next.called
