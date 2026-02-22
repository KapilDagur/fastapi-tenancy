"""Unit tests for fastapi_tenancy.core.context"""

from __future__ import annotations

import asyncio

import pytest

from fastapi_tenancy.core.context import (
    TenantContext,
    get_current_tenant,
    get_current_tenant_optional,
    tenant_scope,
)
from fastapi_tenancy.core.exceptions import TenantNotFoundError
from fastapi_tenancy.core.types import Tenant


def _make_tenant(tid: str = "t1", ident: str = "acme-corp") -> Tenant:
    return Tenant(id=tid, identifier=ident, name="Acme")


class TestTenantContextSet:
    def test_set_and_get(self):
        t = _make_tenant()
        token = TenantContext.set(t)
        assert TenantContext.get() is t
        TenantContext.reset(token)

    def test_reset_restores_none(self):
        TenantContext.clear()
        t = _make_tenant()
        token = TenantContext.set(t)
        TenantContext.reset(token)
        with pytest.raises(TenantNotFoundError):
            TenantContext.get()

    def test_get_optional_returns_none_when_unset(self):
        TenantContext.clear()
        assert TenantContext.get_optional() is None

    def test_get_optional_returns_tenant_when_set(self):
        t = _make_tenant()
        token = TenantContext.set(t)
        assert TenantContext.get_optional() is t
        TenantContext.reset(token)

    def test_get_raises_when_unset(self):
        TenantContext.clear()
        with pytest.raises(TenantNotFoundError) as exc_info:
            TenantContext.get()
        assert "hint" in exc_info.value.details

    def test_clear_clears_both_tenant_and_metadata(self):
        t = _make_tenant()
        TenantContext.set(t)
        TenantContext.set_metadata("key", "val")
        TenantContext.clear()
        assert TenantContext.get_optional() is None
        assert TenantContext.get_metadata("key") is None


class TestTenantContextMetadata:
    def setup_method(self):
        TenantContext.clear()

    def test_set_and_get_metadata(self):
        TenantContext.set_metadata("request_id", "req-123")
        assert TenantContext.get_metadata("request_id") == "req-123"

    def test_get_missing_key_returns_default(self):
        result = TenantContext.get_metadata("missing_key", default="fallback")
        assert result == "fallback"

    def test_get_missing_key_returns_none_by_default(self):
        result = TenantContext.get_metadata("nope")
        assert result is None

    def test_get_metadata_when_none_set(self):
        assert TenantContext.get_metadata("k") is None

    def test_multiple_keys(self):
        TenantContext.set_metadata("a", 1)
        TenantContext.set_metadata("b", 2)
        assert TenantContext.get_metadata("a") == 1
        assert TenantContext.get_metadata("b") == 2

    def test_overwrite_key(self):
        TenantContext.set_metadata("k", "v1")
        TenantContext.set_metadata("k", "v2")
        assert TenantContext.get_metadata("k") == "v2"

    def test_get_all_metadata(self):
        TenantContext.set_metadata("x", 10)
        TenantContext.set_metadata("y", 20)
        all_meta = TenantContext.get_all_metadata()
        assert all_meta == {"x": 10, "y": 20}

    def test_get_all_metadata_empty(self):
        assert TenantContext.get_all_metadata() == {}

    def test_mutating_returned_dict_does_not_affect_context(self):
        TenantContext.set_metadata("k", "v")
        meta = TenantContext.get_all_metadata()
        meta["extra"] = "injected"
        assert TenantContext.get_metadata("extra") is None

    def test_clear_metadata(self):
        TenantContext.set_metadata("k", "v")
        TenantContext.clear_metadata()
        assert TenantContext.get_metadata("k") is None


class TestTenantScope:
    @pytest.mark.asyncio
    async def test_sets_and_restores(self):
        TenantContext.clear()
        t = _make_tenant("inner", "inner-tenant")
        async with tenant_scope(t) as yielded:
            assert yielded is t
            assert TenantContext.get() is t
        with pytest.raises(TenantNotFoundError):
            TenantContext.get()

    @pytest.mark.asyncio
    async def test_nested_scopes(self):
        outer = _make_tenant("outer", "outer-tenant")
        inner = _make_tenant("inner", "inner-tenant")
        async with tenant_scope(outer):
            async with tenant_scope(inner):
                assert TenantContext.get() is inner
            assert TenantContext.get() is outer

    @pytest.mark.asyncio
    async def test_restores_on_exception(self):
        TenantContext.clear()
        t = _make_tenant()
        with pytest.raises(RuntimeError):
            async with tenant_scope(t):
                raise RuntimeError("boom")
        with pytest.raises(TenantNotFoundError):
            TenantContext.get()

    @pytest.mark.asyncio
    async def test_clears_metadata_on_entry(self):
        TenantContext.set_metadata("stale", "value")
        t = _make_tenant()
        async with tenant_scope(t):
            assert TenantContext.get_metadata("stale") is None

    @pytest.mark.asyncio
    async def test_task_isolation(self):
        """Each asyncio task has its own context copy."""
        results = []

        async def task_fn(t: Tenant) -> None:
            async with tenant_scope(t):
                await asyncio.sleep(0)
                results.append(TenantContext.get().id)

        t1 = _make_tenant("task-1", "task-one")
        t2 = _make_tenant("task-2", "task-two")
        await asyncio.gather(task_fn(t1), task_fn(t2))
        assert set(results) == {"task-1", "task-2"}


class TestDependencyFunctions:
    def test_get_current_tenant_raises_when_unset(self):
        TenantContext.clear()
        with pytest.raises(TenantNotFoundError):
            get_current_tenant()

    def test_get_current_tenant_returns_tenant(self):
        t = _make_tenant()
        token = TenantContext.set(t)
        assert get_current_tenant() is t
        TenantContext.reset(token)

    def test_get_current_tenant_optional_none(self):
        TenantContext.clear()
        assert get_current_tenant_optional() is None

    def test_get_current_tenant_optional_returns_tenant(self):
        t = _make_tenant()
        token = TenantContext.set(t)
        assert get_current_tenant_optional() is t
        TenantContext.reset(token)
