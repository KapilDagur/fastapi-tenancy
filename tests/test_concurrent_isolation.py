"""Tests verifying that contextvars isolate tenant context across concurrent tasks.

This is a critical correctness test: the fundamental guarantee of
TenantContext is that two concurrent async tasks cannot see each other's
tenant.  These tests encode and enforce that guarantee.
"""
from __future__ import annotations

import asyncio

import pytest

from fastapi_tenancy.core.context import TenantContext
from fastapi_tenancy.core.exceptions import TenantNotFoundError
from fastapi_tenancy.core.types import Tenant, TenantStatus
from fastapi_tenancy.storage.memory import InMemoryTenantStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> InMemoryTenantStore:
    return InMemoryTenantStore()


@pytest.fixture
async def tenants(store: InMemoryTenantStore) -> tuple[Tenant, Tenant]:
    t1 = Tenant(id="t1-001", identifier="tenant-one", name="Tenant One")
    t2 = Tenant(id="t2-002", identifier="tenant-two", name="Tenant Two")
    await store.create(t1)
    await store.create(t2)
    return t1, t2


# ---------------------------------------------------------------------------
# Core isolation tests
# ---------------------------------------------------------------------------


class TestConcurrentTenantIsolation:
    """Verify that contextvars prevent cross-request tenant leakage."""

    @pytest.mark.asyncio
    async def test_concurrent_tasks_see_own_tenant(
        self, tenants: tuple[Tenant, Tenant]
    ) -> None:
        """Two concurrent coroutines each see only their own tenant."""
        t1, t2 = tenants
        results: dict[str, str] = {}

        async def run(tenant: Tenant) -> None:
            async with TenantContext.scope(tenant):
                # Simulate work that takes time — other coroutine runs here
                await asyncio.sleep(0.01)
                observed = TenantContext.get()
                results[tenant.id] = observed.id

        await asyncio.gather(run(t1), run(t2))

        assert results == {t1.id: t1.id, t2.id: t2.id}, (
            "Each task must observe only its own tenant; "
            "cross-task leakage detected: %s" % results
        )

    @pytest.mark.asyncio
    async def test_many_concurrent_tasks_no_leakage(self) -> None:
        """Ten concurrent tasks — no leakage between any pair."""
        tenant_count = 10
        tenants_list = [
            Tenant(
                id=f"tenant-{i:03d}",
                identifier=f"tenant-{i:03d}",
                name=f"Tenant {i}",
            )
            for i in range(tenant_count)
        ]
        results: dict[str, str] = {}

        async def run(t: Tenant) -> None:
            async with TenantContext.scope(t):
                await asyncio.sleep(0)  # yield to event loop
                results[t.id] = TenantContext.get().id

        await asyncio.gather(*(run(t) for t in tenants_list))

        for t in tenants_list:
            assert results[t.id] == t.id, (
                f"Tenant {t.id} saw {results[t.id]} instead of itself"
            )

    @pytest.mark.asyncio
    async def test_context_cleared_after_scope(self, tenants: tuple[Tenant, Tenant]) -> None:
        """TenantContext is empty after a scope exits."""
        t1, _ = tenants

        async with TenantContext.scope(t1):
            assert TenantContext.get().id == t1.id

        # Context must be cleared after scope exits
        assert TenantContext.get_optional() is None

    @pytest.mark.asyncio
    async def test_nested_scopes_restore_outer(self, tenants: tuple[Tenant, Tenant]) -> None:
        """Nested scopes restore the outer tenant on exit."""
        t1, t2 = tenants

        async with TenantContext.scope(t1):
            assert TenantContext.get().id == t1.id

            async with TenantContext.scope(t2):
                assert TenantContext.get().id == t2.id

            # Back to outer scope
            assert TenantContext.get().id == t1.id

        assert TenantContext.get_optional() is None

    @pytest.mark.asyncio
    async def test_exception_inside_scope_clears_context(
        self, tenants: tuple[Tenant, Tenant]
    ) -> None:
        """Context is cleared even when an exception propagates out of scope."""
        t1, _ = tenants

        with pytest.raises(ValueError, match="test error"):
            async with TenantContext.scope(t1):
                raise ValueError("test error")

        assert TenantContext.get_optional() is None

    @pytest.mark.asyncio
    async def test_metadata_isolated_per_scope(
        self, tenants: tuple[Tenant, Tenant]
    ) -> None:
        """Metadata set in one scope is not visible in a concurrent scope."""
        t1, t2 = tenants
        metadata_seen: dict[str, object] = {}

        async def run_t1() -> None:
            async with TenantContext.scope(t1):
                TenantContext.set_metadata("role", "admin")
                await asyncio.sleep(0.01)
                metadata_seen["t1_role"] = TenantContext.get_metadata("role")

        async def run_t2() -> None:
            async with TenantContext.scope(t2):
                await asyncio.sleep(0)  # don't set role
                metadata_seen["t2_role"] = TenantContext.get_metadata("role", "none")

        await asyncio.gather(run_t1(), run_t2())

        assert metadata_seen["t1_role"] == "admin"
        assert metadata_seen["t2_role"] == "none", (
            "t2 must not see t1's metadata"
        )


# ---------------------------------------------------------------------------
# Metadata ContextVar default safety
# ---------------------------------------------------------------------------


class TestContextVarDefaultSafety:
    """Ensure the ContextVar default=None doesn't cause cross-context pollution."""

    @pytest.mark.asyncio
    async def test_fresh_context_has_no_metadata(self) -> None:
        """A freshly entered scope has an empty metadata dict."""
        t = Tenant(id="meta-test-001", identifier="meta-test", name="Meta Test")
        async with TenantContext.scope(t):
            # get_all_metadata should return {} not a shared mutable default
            meta = TenantContext.get_all_metadata()
            assert meta == {}
            assert meta is not TenantContext.get_all_metadata(), (
                "Each call to get_all_metadata must return a fresh copy"
            )

    @pytest.mark.asyncio
    async def test_mutating_returned_metadata_does_not_affect_context(self) -> None:
        """Mutating the dict returned by get_all_metadata has no side effects."""
        t = Tenant(id="meta-mut-001", identifier="meta-mut", name="Meta Mut")
        async with TenantContext.scope(t):
            TenantContext.set_metadata("key", "value")
            meta = TenantContext.get_all_metadata()
            meta["injected"] = "evil"  # mutate the returned copy

            # The context itself must not have been modified
            fresh = TenantContext.get_all_metadata()
            assert "injected" not in fresh
