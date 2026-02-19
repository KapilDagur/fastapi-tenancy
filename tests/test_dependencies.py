"""Dependencies: get_tenant_db, require_active_tenant, get_tenant_config."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from fastapi_tenancy.core.types import Tenant, TenantStatus


def _make_tenant(status: TenantStatus = TenantStatus.ACTIVE) -> Tenant:
    return Tenant(id="t1", identifier="acme", name="Acme", status=status)


def _make_request(tenant_id="t1"):
    req = MagicMock()
    isolation = AsyncMock()

    async def mock_get_session(tenant):
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def _ctx():
            yield MagicMock()
        return _ctx()

    req.app.state.isolation_provider.get_session = mock_get_session
    return req


class TestRequireActiveTenant:

    @pytest.mark.asyncio
    async def test_active_tenant_passes(self) -> None:
        from fastapi_tenancy.dependencies import require_active_tenant
        t = _make_tenant(TenantStatus.ACTIVE)
        result = await require_active_tenant(tenant=t)
        assert result == t

    @pytest.mark.asyncio
    async def test_suspended_tenant_raises_403(self) -> None:
        from fastapi import HTTPException

        from fastapi_tenancy.dependencies import require_active_tenant
        t = _make_tenant(TenantStatus.SUSPENDED)
        with pytest.raises(HTTPException) as exc_info:
            await require_active_tenant(tenant=t)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_provisioning_tenant_raises_403(self) -> None:
        from fastapi import HTTPException

        from fastapi_tenancy.dependencies import require_active_tenant
        t = _make_tenant(TenantStatus.PROVISIONING)
        with pytest.raises(HTTPException) as exc_info:
            await require_active_tenant(tenant=t)
        assert exc_info.value.status_code == 403


class TestGetTenantConfig:

    @pytest.mark.asyncio
    async def test_returns_tenant_config(self) -> None:
        from fastapi_tenancy.core.types import TenantConfig
        from fastapi_tenancy.dependencies import get_tenant_config
        t = Tenant(
            id="t1", identifier="acme", name="Acme",
            metadata={
                "max_users": 50,
                "max_storage_gb": 10.0,
                "features_enabled": ["sso"],
                "rate_limit_per_minute": 200,
                "custom_settings": {"theme": "dark"},
            }
        )
        cfg = await get_tenant_config(tenant=t)
        assert isinstance(cfg, TenantConfig)
        assert cfg.max_users == 50
        assert cfg.max_storage_gb == 10.0
        assert "sso" in cfg.features_enabled
        assert cfg.rate_limit_per_minute == 200
        assert cfg.custom_settings["theme"] == "dark"

    @pytest.mark.asyncio
    async def test_defaults_when_no_metadata(self) -> None:
        from fastapi_tenancy.dependencies import get_tenant_config
        t = Tenant(id="t1", identifier="acme", name="Acme")
        cfg = await get_tenant_config(tenant=t)
        assert cfg.max_users is None
        assert cfg.rate_limit_per_minute == 100
        assert cfg.features_enabled == []
        assert cfg.custom_settings == {}


class TestGetTenantDb:

    @pytest.mark.asyncio
    async def test_yields_session(self) -> None:
        from contextlib import asynccontextmanager

        from fastapi_tenancy.dependencies import get_tenant_db

        mock_session = MagicMock()
        t = _make_tenant()

        @asynccontextmanager
        async def mock_get_session(tenant):
            yield mock_session

        req = MagicMock()
        req.app.state.isolation_provider.get_session = mock_get_session

        gen = get_tenant_db(tenant=t, request=req)
        session = await gen.__anext__()
        assert session is mock_session
        try:
            await gen.__anext__()
        except StopAsyncIteration:
            pass

    @pytest.mark.asyncio
    async def test_uses_isolation_provider_from_request(self) -> None:
        from contextlib import asynccontextmanager

        from fastapi_tenancy.dependencies import get_tenant_db

        called_with = []

        @asynccontextmanager
        async def mock_get_session(tenant):
            called_with.append(tenant)
            yield MagicMock()

        req = MagicMock()
        req.app.state.isolation_provider.get_session = mock_get_session
        t = _make_tenant()

        gen = get_tenant_db(tenant=t, request=req)
        await gen.__anext__()
        assert t in called_with
