"""Unit tests for tenant context management."""

import pytest

from fastapi_tenancy.core.context import TenantContext, get_current_tenant
from fastapi_tenancy.core.exceptions import TenantNotFoundError
from fastapi_tenancy.core.types import Tenant


class TestTenantContext:
    """Test suite for TenantContext."""

    def test_set_and_get_tenant(self, test_tenant: Tenant) -> None:
        """Test setting and getting tenant from context."""
        TenantContext.set(test_tenant)
        retrieved = TenantContext.get()

        assert retrieved == test_tenant
        assert retrieved.id == test_tenant.id
        assert retrieved.identifier == test_tenant.identifier

    def test_get_without_tenant_raises_error(self) -> None:
        """Test getting tenant when none is set raises error."""
        with pytest.raises(TenantNotFoundError):
            TenantContext.get()

    def test_get_optional_returns_none(self) -> None:
        """Test get_optional returns None when no tenant is set."""
        result = TenantContext.get_optional()
        assert result is None

    def test_get_optional_returns_tenant(self, test_tenant: Tenant) -> None:
        """Test get_optional returns tenant when one is set."""
        TenantContext.set(test_tenant)
        result = TenantContext.get_optional()

        assert result == test_tenant

    def test_clear_tenant(self, test_tenant: Tenant) -> None:
        """Test clearing tenant from context."""
        TenantContext.set(test_tenant)
        assert TenantContext.get_optional() is not None

        TenantContext.clear()
        assert TenantContext.get_optional() is None

    def test_reset_tenant(self, test_tenant: Tenant, secondary_tenant: Tenant) -> None:
        """Test resetting tenant to previous state."""
        # Set first tenant
        TenantContext.set(test_tenant)
        assert TenantContext.get().id == test_tenant.id

        # Set second tenant
        token2 = TenantContext.set(secondary_tenant)
        assert TenantContext.get().id == secondary_tenant.id

        # Reset second tenant, should restore first
        TenantContext.reset(token2)
        assert TenantContext.get().id == test_tenant.id

    def test_metadata_operations(self, test_tenant: Tenant) -> None:
        """Test metadata set and get operations."""
        TenantContext.set(test_tenant)

        # Set metadata
        TenantContext.set_metadata("user_id", "user123")
        TenantContext.set_metadata("permissions", ["read", "write"])

        # Get metadata
        assert TenantContext.get_metadata("user_id") == "user123"
        assert TenantContext.get_metadata("permissions") == ["read", "write"]
        assert TenantContext.get_metadata("nonexistent") is None
        assert TenantContext.get_metadata("nonexistent", "default") == "default"

    def test_clear_metadata(self, test_tenant: Tenant) -> None:
        """Test clearing metadata."""
        TenantContext.set(test_tenant)
        TenantContext.set_metadata("key", "value")

        assert TenantContext.get_metadata("key") == "value"

        TenantContext.clear_metadata()
        assert TenantContext.get_metadata("key") is None

    @pytest.mark.asyncio()
    async def test_async_scope_context_manager(self, test_tenant: Tenant) -> None:
        """Test async context manager for tenant scope."""
        # Before scope
        assert TenantContext.get_optional() is None

        # Inside scope
        async with TenantContext.scope(test_tenant) as tenant:
            assert tenant == test_tenant
            assert TenantContext.get() == test_tenant

        # After scope
        assert TenantContext.get_optional() is None

    def test_sync_scope_context_manager(self, test_tenant: Tenant) -> None:
        """Test sync context manager for tenant scope."""
        # Before scope
        assert TenantContext.get_optional() is None

        # Inside scope
        with TenantContext.scope(test_tenant) as tenant:
            assert tenant == test_tenant
            assert TenantContext.get() == test_tenant

        # After scope
        assert TenantContext.get_optional() is None

    @pytest.mark.asyncio()
    async def test_scope_cleans_up_on_exception(self, test_tenant: Tenant) -> None:
        """Test that scope cleans up even when exception occurs."""
        with pytest.raises(ValueError):  # noqa: PT011, PT012
            async with TenantContext.scope(test_tenant):
                assert TenantContext.get() == test_tenant
                msg = "Test error"
                raise ValueError(msg)

        # Context should be cleared even after exception
        assert TenantContext.get_optional() is None

    @pytest.mark.asyncio()
    async def test_nested_scopes(self, test_tenant: Tenant, secondary_tenant: Tenant) -> None:
        """Test nested tenant scopes."""
        async with TenantContext.scope(test_tenant):
            assert TenantContext.get().id == test_tenant.id

            async with TenantContext.scope(secondary_tenant):
                assert TenantContext.get().id == secondary_tenant.id

            # Should be back to first tenant
            assert TenantContext.get().id == test_tenant.id

        # Should be cleared
        assert TenantContext.get_optional() is None

    def test_dependency_function(self, test_tenant: Tenant) -> None:
        """Test get_current_tenant dependency function."""
        TenantContext.set(test_tenant)

        tenant = get_current_tenant()
        assert tenant == test_tenant

    def test_dependency_function_raises_without_tenant(self) -> None:
        """Test dependency function raises error when no tenant is set."""
        with pytest.raises(TenantNotFoundError):
            get_current_tenant()

    @pytest.mark.asyncio()
    async def test_concurrent_contexts(self, test_tenant: Tenant, secondary_tenant: Tenant) -> None:
        """Test that contexts are isolated in concurrent execution."""
        import asyncio

        results: list[str] = []

        async def task1() -> None:
            async with TenantContext.scope(test_tenant):
                await asyncio.sleep(0.01)
                results.append(TenantContext.get().id)

        async def task2() -> None:
            async with TenantContext.scope(secondary_tenant):
                await asyncio.sleep(0.01)
                results.append(TenantContext.get().id)

        # Run tasks concurrently
        await asyncio.gather(task1(), task2())

        # Each task should have gotten its own tenant
        assert test_tenant.id in results
        assert secondary_tenant.id in results
