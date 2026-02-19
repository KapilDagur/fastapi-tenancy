"""Tests for TenantContext — async-safe contextvars isolation."""
from __future__ import annotations

import asyncio

import pytest

from fastapi_tenancy.core.context import (
    TenantContext,
    get_current_tenant,
    get_current_tenant_optional,
)
from fastapi_tenancy.core.exceptions import TenantNotFoundError
from fastapi_tenancy.core.types import Tenant


@pytest.fixture(autouse=True)
def clean_context():
    """Ensure TenantContext is clear before and after every test."""
    TenantContext.clear()
    yield
    TenantContext.clear()


def make_tenant(suffix: str = "1") -> Tenant:
    return Tenant(id=f"t-{suffix}", identifier=f"acme-{suffix}", name=f"Acme {suffix}")


class TestBasicGetSet:

    def test_get_raises_when_empty(self) -> None:
        with pytest.raises(TenantNotFoundError):
            TenantContext.get()

    def test_get_optional_returns_none_when_empty(self) -> None:
        assert TenantContext.get_optional() is None

    def test_set_and_get(self) -> None:
        t = make_tenant()
        TenantContext.set(t)
        assert TenantContext.get() == t

    def test_optional_after_set(self) -> None:
        t = make_tenant()
        TenantContext.set(t)
        assert TenantContext.get_optional() == t

    def test_clear_resets_to_empty(self) -> None:
        TenantContext.set(make_tenant())
        TenantContext.clear()
        assert TenantContext.get_optional() is None

    def test_set_returns_token(self) -> None:
        token = TenantContext.set(make_tenant())
        assert token is not None

    def test_reset_with_token(self) -> None:
        """ContextVar.reset(token) restores the value _before_ that set call."""
        t1, t2 = make_tenant("1"), make_tenant("2")
        TenantContext.set(t1)           # state: t1
        token2 = TenantContext.set(t2)  # state: t2, token2 points to "before t2" = t1
        TenantContext.reset(token2)     # restore to t1
        assert TenantContext.get() == t1

    def test_overwrite_previous_value(self) -> None:
        t1, t2 = make_tenant("1"), make_tenant("2")
        TenantContext.set(t1)
        TenantContext.set(t2)
        assert TenantContext.get() == t2


class TestSyncScope:

    def test_scope_yields_tenant(self) -> None:
        t = make_tenant()
        with TenantContext.scope(t) as yielded:
            assert yielded == t

    def test_scope_sets_context(self) -> None:
        t = make_tenant()
        with TenantContext.scope(t):
            assert TenantContext.get() == t

    def test_scope_clears_after_exit(self) -> None:
        with TenantContext.scope(make_tenant()):
            pass
        assert TenantContext.get_optional() is None

    def test_scope_clears_on_exception(self) -> None:
        with pytest.raises(ValueError), TenantContext.scope(make_tenant()):
            raise ValueError("deliberate")
        assert TenantContext.get_optional() is None

    def test_nested_scopes_restore_outer(self) -> None:
        t1, t2 = make_tenant("1"), make_tenant("2")
        with TenantContext.scope(t1):
            assert TenantContext.get() == t1
            with TenantContext.scope(t2):
                assert TenantContext.get() == t2
            assert TenantContext.get() == t1
        assert TenantContext.get_optional() is None

    def test_scope_preserves_outer_context(self) -> None:
        t_outer = make_tenant("outer")
        TenantContext.set(t_outer)
        with TenantContext.scope(make_tenant("inner")):
            pass
        # After scope exits, outer value is restored
        assert TenantContext.get() == t_outer


class TestAsyncScope:

    @pytest.mark.asyncio
    async def test_async_scope_yields(self) -> None:
        t = make_tenant()
        async with TenantContext.scope(t) as yielded:
            assert yielded == t

    @pytest.mark.asyncio
    async def test_async_scope_sets_context(self) -> None:
        t = make_tenant()
        async with TenantContext.scope(t):
            assert TenantContext.get() == t

    @pytest.mark.asyncio
    async def test_async_scope_clears_after(self) -> None:
        async with TenantContext.scope(make_tenant()):
            pass
        assert TenantContext.get_optional() is None

    @pytest.mark.asyncio
    async def test_async_scope_clears_on_exception(self) -> None:
        with pytest.raises(RuntimeError):
            async with TenantContext.scope(make_tenant()):
                raise RuntimeError("boom")
        assert TenantContext.get_optional() is None

    @pytest.mark.asyncio
    async def test_async_nested_scopes(self) -> None:
        t1, t2 = make_tenant("1"), make_tenant("2")
        async with TenantContext.scope(t1):
            async with TenantContext.scope(t2):
                assert TenantContext.get() == t2
            assert TenantContext.get() == t1


class TestConcurrentIsolation:
    """CRITICAL: concurrent async tasks must be fully isolated."""

    @pytest.mark.asyncio
    async def test_concurrent_tasks_isolated(self) -> None:
        results: dict[str, str] = {}
        errors: list[str] = []

        async def run_tenant(tid: str, sleep: float) -> None:
            t = make_tenant(tid)
            async with TenantContext.scope(t):
                await asyncio.sleep(sleep)
                got = TenantContext.get()
                if got.id != t.id:
                    errors.append(f"Task {tid}: got {got.id!r}, expected {t.id!r}")
                results[tid] = got.id

        await asyncio.gather(
            run_tenant("a", 0.010),
            run_tenant("b", 0.005),
            run_tenant("c", 0.001),
        )

        assert not errors, f"Context leaked between tasks: {errors}"
        assert results == {"a": "t-a", "b": "t-b", "c": "t-c"}

    @pytest.mark.asyncio
    async def test_many_concurrent_tasks(self) -> None:
        N = 20
        results: list[str] = []

        async def run(i: int) -> None:
            t = make_tenant(str(i))
            async with TenantContext.scope(t):
                await asyncio.sleep(0.001)
                results.append(TenantContext.get().id)

        await asyncio.gather(*[run(i) for i in range(N)])
        assert len(results) == N

    @pytest.mark.asyncio
    async def test_task_independence(self) -> None:
        """Setting context in one task must not affect another."""
        barrier = asyncio.Event()
        seen_in_b: list = []

        async def task_a() -> None:
            TenantContext.set(make_tenant("from-a"))
            barrier.set()
            await asyncio.sleep(0.010)

        async def task_b() -> None:
            await barrier.wait()
            # task_a set its context — task_b should see nothing
            seen_in_b.append(TenantContext.get_optional())

        await asyncio.gather(task_a(), task_b())
        assert seen_in_b[0] is None, "Context leaked from task_a into task_b!"


class TestMetadata:

    def test_set_and_get_metadata(self) -> None:
        TenantContext.set(make_tenant())
        TenantContext.set_metadata("plan", "enterprise")
        assert TenantContext.get_metadata("plan") == "enterprise"

    def test_get_missing_metadata_default(self) -> None:
        TenantContext.set(make_tenant())
        assert TenantContext.get_metadata("missing") is None
        assert TenantContext.get_metadata("missing", default="fb") == "fb"

    def test_get_all_metadata(self) -> None:
        TenantContext.set(make_tenant())
        TenantContext.set_metadata("a", 1)
        TenantContext.set_metadata("b", 2)
        meta = TenantContext.get_all_metadata()
        assert meta.get("a") == 1
        assert meta.get("b") == 2

    def test_clear_metadata(self) -> None:
        TenantContext.set(make_tenant())
        TenantContext.set_metadata("x", "y")
        TenantContext.clear_metadata()
        assert TenantContext.get_metadata("x") is None
        # Tenant itself still set
        assert TenantContext.get_optional() is not None

    def test_metadata_cleared_on_context_clear(self) -> None:
        TenantContext.set(make_tenant())
        TenantContext.set_metadata("key", "val")
        TenantContext.clear()
        # After full clear, metadata gone
        TenantContext.set(make_tenant("new"))
        assert TenantContext.get_metadata("key") is None


class TestDependencies:

    def test_get_current_tenant_dep(self) -> None:
        t = make_tenant()
        TenantContext.set(t)
        assert get_current_tenant() == t

    def test_get_current_tenant_raises_empty(self) -> None:
        with pytest.raises(TenantNotFoundError):
            get_current_tenant()

    def test_get_current_tenant_optional_dep(self) -> None:
        t = make_tenant()
        TenantContext.set(t)
        assert get_current_tenant_optional() == t

    def test_get_current_tenant_optional_empty(self) -> None:
        assert get_current_tenant_optional() is None
