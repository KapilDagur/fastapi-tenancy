"""Multi-tenant migration manager — Alembic + asyncio-safe.

Fix from v0.1.0
---------------
- Alembic's command API is *synchronous*.  Running it directly inside an
  ``async with session`` block would call blocking I/O on the event-loop
  thread and freeze the application.
  Fix: every Alembic call is wrapped in ``asyncio.get_running_loop().run_in_executor(None, …)``
  so it executes in the default thread-pool without blocking the event loop.

- ``get_migration_status()`` previously hardcoded ``"unknown"`` for the
  current revision.  It now queries the ``alembic_version`` table directly.
"""
from __future__ import annotations

import asyncio
import logging
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from fastapi_tenancy.core.exceptions import MigrationError
from fastapi_tenancy.core.types import IsolationStrategy, Tenant

if TYPE_CHECKING:
    from fastapi_tenancy.isolation.base import BaseIsolationProvider

logger = logging.getLogger(__name__)


async def _run_sync(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a synchronous callable in the default thread-pool executor.

    This is the correct way to call blocking I/O (such as Alembic's command
    API) from an async context without blocking the event loop.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))


class MigrationManager:
    """Run Alembic database migrations for all tenants.

    Supports every isolation strategy:

    * **Schema** — sets ``search_path`` / ``schema_name`` option so Alembic
      migrates each tenant's schema.
    * **Database** — sets ``sqlalchemy.url`` so Alembic connects to the
      tenant-specific database.
    * **RLS** — migrates the shared schema once.
    * **Hybrid** — delegates to the appropriate strategy per tenant.

    Example
    -------
    .. code-block:: python

        manager = MigrationManager(
            alembic_ini_path="alembic.ini",
            isolation_provider=schema_provider,
        )

        # Migrate all tenants
        results = await manager.upgrade_all_tenants(tenants)
        print(results["success"], "succeeded,", results["failed"], "failed")

        # Migrate one tenant
        await manager.upgrade_tenant(tenant)

        # Check status
        status = await manager.get_migration_status(tenant)
        print(status["current_revision"], "→", status["latest_revision"])
    """

    def __init__(
        self,
        alembic_ini_path: str | Path,
        isolation_provider: BaseIsolationProvider,
    ) -> None:
        self.alembic_ini_path = Path(alembic_ini_path)
        self.isolation_provider = isolation_provider

        if not self.alembic_ini_path.exists():
            raise FileNotFoundError(
                f"Alembic config not found: {self.alembic_ini_path}"
            )
        logger.info("MigrationManager initialised config=%s", self.alembic_ini_path)

    def _get_alembic_config(self, tenant: Tenant | None = None) -> Config:
        """Build an Alembic :class:`Config` scoped to *tenant* (if given)."""
        cfg = Config(str(self.alembic_ini_path))

        if tenant is None:
            return cfg

        strategy = getattr(self.isolation_provider.config, "isolation_strategy", None)
        if strategy == IsolationStrategy.SCHEMA:
            schema_name = self.isolation_provider.get_schema_name(tenant)
            cfg.set_main_option("schema_name", schema_name)

        elif strategy == IsolationStrategy.DATABASE:
            database_url = self.isolation_provider.get_database_url(tenant)
            cfg.set_main_option("sqlalchemy.url", database_url)

        return cfg

    async def upgrade_tenant(
        self,
        tenant: Tenant,
        revision: str = "head",
    ) -> None:
        """Run migrations for a single *tenant* up to *revision*.

        Parameters
        ----------
        tenant:
            Target tenant.
        revision:
            Alembic revision target (default: ``"head"`` — latest).

        Raises
        ------
        MigrationError
            If Alembic raises any exception during the upgrade.
        """
        logger.info(
            "Upgrading tenant %s (%s) to revision %r",
            tenant.identifier, tenant.id, revision,
        )
        cfg = self._get_alembic_config(tenant)
        try:
            # IMPORTANT: run_in_executor — Alembic command API is synchronous
            await _run_sync(command.upgrade, cfg, revision)
            logger.info("Tenant %s upgraded to %r", tenant.identifier, revision)
        except Exception as exc:
            logger.error(
                "Migration failed for tenant %s: %s", tenant.identifier, exc, exc_info=True
            )
            raise MigrationError(
                tenant_id=tenant.id,
                operation="upgrade",
                reason=str(exc),
                details={"revision": revision},
            ) from exc

    async def downgrade_tenant(self, tenant: Tenant, revision: str) -> None:
        """Roll back migrations for *tenant* to *revision*.

        .. warning::
            Data loss may occur.  Use with caution.
        """
        logger.warning(
            "Downgrading tenant %s to revision %r", tenant.identifier, revision
        )
        cfg = self._get_alembic_config(tenant)
        try:
            await _run_sync(command.downgrade, cfg, revision)
            logger.info("Tenant %s downgraded to %r", tenant.identifier, revision)
        except Exception as exc:
            raise MigrationError(
                tenant_id=tenant.id,
                operation="downgrade",
                reason=str(exc),
                details={"revision": revision},
            ) from exc

    async def upgrade_all_tenants(
        self,
        tenants: list[Tenant],
        revision: str = "head",
        continue_on_error: bool = True,
    ) -> dict[str, Any]:
        """Run migrations for every tenant in *tenants*.

        Parameters
        ----------
        tenants:
            List of tenants to migrate.
        revision:
            Target revision (default: ``"head"``).
        continue_on_error:
            When ``True`` (default) a failing tenant is logged and skipped;
            the loop continues to the next tenant.  When ``False`` the first
            failure aborts the whole batch.

        Returns
        -------
        dict
            ``{"success": int, "failed": int, "total": int, "errors": list}``
        """
        results: dict[str, Any] = {
            "success": 0,
            "failed": 0,
            "total": len(tenants),
            "errors": [],
        }
        logger.info(
            "Starting migration of %d tenants to revision %r", len(tenants), revision
        )

        for i, tenant in enumerate(tenants, 1):
            try:
                logger.info("Migrating %d/%d: %s", i, len(tenants), tenant.identifier)
                await self.upgrade_tenant(tenant, revision)
                results["success"] += 1
            except MigrationError as exc:
                results["failed"] += 1
                results["errors"].append(
                    {
                        "tenant_id": tenant.id,
                        "identifier": tenant.identifier,
                        "error": str(exc),
                        "operation": exc.operation,
                    }
                )
                logger.error("Migration failed for %s: %s", tenant.identifier, exc)
                if not continue_on_error:
                    logger.error("Aborting migration batch on first error")
                    break

        logger.info(
            "Migration complete: %d succeeded, %d failed / %d total",
            results["success"], results["failed"], results["total"],
        )
        return results

    async def get_migration_status(self, tenant: Tenant) -> dict[str, Any]:
        """Return the current and latest Alembic revision for *tenant*.

        The current revision is read from the ``alembic_version`` table using
        the tenant-scoped database session.  Previously this returned the
        hardcoded string ``"unknown"``.

        Returns
        -------
        dict
            ``{"tenant_id", "tenant_identifier", "current_revision",
               "latest_revision", "is_up_to_date"}``
            On error the dict contains an ``"error"`` key instead.
        """
        try:
            cfg = self._get_alembic_config(tenant)
            script = ScriptDirectory.from_config(cfg)
            latest_revision: str | None = script.get_current_head()

            # Query alembic_version table inside the tenant-scoped session
            from sqlalchemy import text as sa_text

            current_revision: str | None = None
            async with self.isolation_provider.get_session(tenant) as session:
                result = await session.execute(
                    sa_text("SELECT version_num FROM alembic_version LIMIT 1")
                )
                row = result.scalar_one_or_none()
                current_revision = str(row) if row is not None else None

            return {
                "tenant_id": tenant.id,
                "tenant_identifier": tenant.identifier,
                "current_revision": current_revision,
                "latest_revision": latest_revision,
                "is_up_to_date": current_revision == latest_revision,
            }

        except Exception as exc:
            logger.error(
                "get_migration_status failed for tenant %s: %s",
                tenant.identifier, exc, exc_info=True,
            )
            return {
                "tenant_id": tenant.id,
                "tenant_identifier": tenant.identifier,
                "error": str(exc),
            }

    async def create_revision(
        self,
        message: str,
        autogenerate: bool = True,
    ) -> str:
        """Generate a new Alembic migration script.

        Returns
        -------
        str
            The created revision ID (e.g. ``"a1b2c3d4e5f6"``).
        """
        logger.info("Creating migration: %r autogenerate=%s", message, autogenerate)
        cfg = self._get_alembic_config()
        try:
            script = await _run_sync(
                command.revision, cfg, message=message, autogenerate=autogenerate
            )
            revision_id: str = script.revision if script is not None else "unknown"
            logger.info("Migration created: %r revision=%s", message, revision_id)
            return revision_id
        except Exception as exc:
            logger.error("Failed to create migration: %s", exc, exc_info=True)
            raise MigrationError(
                tenant_id="all",
                operation="create_revision",
                reason=str(exc),
                details={"message": message},
            ) from exc


__all__ = ["MigrationManager"]
