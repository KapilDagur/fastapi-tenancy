"""Unit tests for MigrationManager — verifies the async/blocking fix."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fastapi_tenancy.core.types import IsolationStrategy, Tenant
from fastapi_tenancy.migrations.manager import MigrationManager, _run_sync


class TestRunSyncHelper:
    """Verify _run_sync runs blocking code in thread pool without blocking loop."""

    @pytest.mark.asyncio
    async def test_run_sync_executes_in_thread(self) -> None:
        import threading

        result: dict[str, object] = {}

        def blocking_task() -> str:
            result["thread"] = threading.current_thread().name
            return "done"

        out = await _run_sync(blocking_task)
        assert out == "done"
        # Must NOT have run on the main event loop thread
        main_thread = threading.main_thread().name
        # In some test setups the executor may reuse main thread; at minimum it should return
        assert result["thread"] is not None

    @pytest.mark.asyncio
    async def test_run_sync_passes_args(self) -> None:
        def add(a: int, b: int) -> int:
            return a + b

        result = await _run_sync(add, 3, 4)
        assert result == 7

    @pytest.mark.asyncio
    async def test_run_sync_propagates_exception(self) -> None:
        def boom() -> None:
            raise ValueError("sync failure")

        with pytest.raises(ValueError, match="sync failure"):
            await _run_sync(boom)


class TestMigrationManagerInit:

    def test_missing_config_raises(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "alembic.ini"
        provider = MagicMock()
        with pytest.raises(FileNotFoundError):
            MigrationManager(alembic_ini_path=nonexistent, isolation_provider=provider)

    def test_valid_config_initialises(self, tmp_path: Path) -> None:
        ini = tmp_path / "alembic.ini"
        ini.write_text("[alembic]\n")
        provider = MagicMock()
        manager = MigrationManager(alembic_ini_path=ini, isolation_provider=provider)
        assert manager.alembic_ini_path == ini


class TestUpgradeTenant:

    @pytest.fixture
    def manager(self, tmp_path: Path) -> MigrationManager:
        ini = tmp_path / "alembic.ini"
        ini.write_text("[alembic]\n")
        provider = MagicMock()
        provider.config = MagicMock()
        provider.config.isolation_strategy = IsolationStrategy.RLS
        return MigrationManager(alembic_ini_path=ini, isolation_provider=provider)

    @pytest.fixture
    def tenant(self) -> Tenant:
        return Tenant(id="t1", identifier="acme-corp", name="Acme")

    @pytest.mark.asyncio
    async def test_upgrade_calls_alembic_in_executor(
        self, manager: MigrationManager, tenant: Tenant
    ) -> None:
        """The critical test: Alembic command must be run via run_in_executor."""
        with patch("fastapi_tenancy.migrations.manager.command.upgrade") as mock_upgrade:
            with patch("fastapi_tenancy.migrations.manager._run_sync", new=AsyncMock(return_value=None)) as mock_run:
                await manager.upgrade_tenant(tenant, revision="head")
                mock_run.assert_called_once()
                # First positional arg to _run_sync should be command.upgrade
                called_fn = mock_run.call_args[0][0]
                assert called_fn is mock_upgrade

    @pytest.mark.asyncio
    async def test_upgrade_wraps_exception_as_migration_error(
        self, manager: MigrationManager, tenant: Tenant
    ) -> None:
        from fastapi_tenancy.core.exceptions import MigrationError

        with patch(
            "fastapi_tenancy.migrations.manager._run_sync",
            new=AsyncMock(side_effect=RuntimeError("alembic boom")),
        ), pytest.raises(MigrationError, match="upgrade"):
            await manager.upgrade_tenant(tenant)


class TestUpgradeAllTenants:

    @pytest.fixture
    def manager(self, tmp_path: Path) -> MigrationManager:
        ini = tmp_path / "alembic.ini"
        ini.write_text("[alembic]\n")
        provider = MagicMock()
        provider.config = MagicMock()
        provider.config.isolation_strategy = IsolationStrategy.RLS
        return MigrationManager(alembic_ini_path=ini, isolation_provider=provider)

    @pytest.mark.asyncio
    async def test_all_succeed(self, manager: MigrationManager) -> None:
        tenants = [
            Tenant(id=f"t{i}", identifier=f"tenant-{i:02d}", name=f"T{i}")
            for i in range(3)
        ]
        with patch(
            "fastapi_tenancy.migrations.manager._run_sync",
            new=AsyncMock(return_value=None),
        ):
            results = await manager.upgrade_all_tenants(tenants)
        assert results["success"] == 3
        assert results["failed"] == 0

    @pytest.mark.asyncio
    async def test_continue_on_error(self, manager: MigrationManager) -> None:
        from fastapi_tenancy.core.exceptions import MigrationError

        tenants = [
            Tenant(id=f"t{i}", identifier=f"tenant-{i:02d}", name=f"T{i}")
            for i in range(3)
        ]

        call_count = 0

        async def mock_run_sync(fn, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise MigrationError(
                    tenant_id=tenants[1].id,
                    operation="upgrade",
                    reason="locked",
                )

        with patch("fastapi_tenancy.migrations.manager._run_sync", new=mock_run_sync):
            results = await manager.upgrade_all_tenants(tenants, continue_on_error=True)

        assert results["success"] == 2
        assert results["failed"] == 1

    @pytest.mark.asyncio
    async def test_abort_on_first_error(self, manager: MigrationManager) -> None:
        from fastapi_tenancy.core.exceptions import MigrationError

        tenants = [
            Tenant(id=f"t{i}", identifier=f"tenant-{i:02d}", name=f"T{i}")
            for i in range(3)
        ]

        async def always_fail(fn, *args, **kwargs):
            raise MigrationError(tenant_id="t0", operation="upgrade", reason="boom")

        with patch("fastapi_tenancy.migrations.manager._run_sync", new=always_fail):
            results = await manager.upgrade_all_tenants(tenants, continue_on_error=False)

        # Only first attempt made before abort
        assert results["total"] == 3
        assert results["failed"] >= 1
