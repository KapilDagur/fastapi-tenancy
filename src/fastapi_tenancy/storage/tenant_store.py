"""Abstract tenant storage interface and repository pattern implementation.

This module defines the repository interface for tenant storage with support
for multiple backend implementations (PostgreSQL, Redis, In-Memory).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from fastapi_tenancy.core.exceptions import TenantNotFoundError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastapi_tenancy.core.types import Tenant, TenantStatus


class TenantStore(ABC):
    """Abstract base class for tenant storage implementations.

    This defines the repository interface that all tenant storage implementations
    must follow. It provides a consistent API for tenant CRUD operations regardless
    of the underlying storage backend.

    The repository pattern provides:
    - Abstraction over data access
    - Easy testing with mock repositories
    - Ability to swap storage backends
    - Clean separation of concerns

    Implementations:
    - PostgreSQLTenantStore: Production persistent storage
    - RedisTenantStore: High-performance caching
    - InMemoryTenantStore: Testing and development

    Example:
        ```python
        # Using PostgreSQL
        store = PostgreSQLTenantStore(database_url="postgresql://...")
        await store.initialize()

        # Create tenant
        tenant = Tenant(id="123", identifier="acme", name="Acme Corp")
        await store.create(tenant)

        # Retrieve tenant
        tenant = await store.get_by_id("123")

        # Update tenant
        updated = tenant.model_copy(update={"name": "Acme Corporation"})
        await store.update(updated)

        # List tenants
        tenants = await store.list(skip=0, limit=10)

        # Delete tenant
        await store.delete("123")
        ```
    """

    @abstractmethod
    async def get_by_id(self, tenant_id: str) -> Tenant:
        """Get tenant by unique ID.

        Args:
            tenant_id: Unique tenant identifier

        Returns:
            Tenant object

        Raises:
            TenantNotFoundError: If tenant does not exist

        Example:
            ```python
            tenant = await store.get_by_id("tenant-123")
            print(f"Found: {tenant.name}")
            ```
        """
        pass

    @abstractmethod
    async def get_by_identifier(self, identifier: str) -> Tenant:
        """Get tenant by human-readable identifier (slug).

        Args:
            identifier: Tenant identifier/slug (e.g., 'acme-corp')

        Returns:
            Tenant object

        Raises:
            TenantNotFoundError: If tenant does not exist

        Example:
            ```python
            tenant = await store.get_by_identifier("acme-corp")
            print(f"Tenant ID: {tenant.id}")
            ```
        """
        pass

    @abstractmethod
    async def create(self, tenant: Tenant) -> Tenant:
        """Create a new tenant.

        Args:
            tenant: Tenant object to create

        Returns:
            Created tenant with any generated fields populated

        Raises:
            ValueError: If tenant with same ID or identifier already exists
            TenancyError: If creation fails

        Example:
            ```python
            tenant = Tenant(
                id="new-123",
                identifier="new-corp",
                name="New Corporation",
                status=TenantStatus.PROVISIONING
            )
            created = await store.create(tenant)
            ```
        """
        pass

    @abstractmethod
    async def update(self, tenant: Tenant) -> Tenant:
        """Update an existing tenant.

        Note: Tenant model is immutable, so create a new instance with
        model_copy(update={...}) before calling this method.

        Args:
            tenant: Tenant object with updated values

        Returns:
            Updated tenant

        Raises:
            TenantNotFoundError: If tenant does not exist
            TenancyError: If update fails

        Example:
            ```python
            # Get current tenant
            tenant = await store.get_by_id("123")

            # Create updated version
            updated = tenant.model_copy(
                update={"name": "New Name", "status": TenantStatus.ACTIVE}
            )

            # Save update
            result = await store.update(updated)
            ```
        """
        pass

    @abstractmethod
    async def delete(self, tenant_id: str) -> None:
        """Delete a tenant.

        Behavior depends on configuration:
        - Soft delete: Marks tenant as deleted (if enable_soft_delete=True)
        - Hard delete: Permanently removes tenant data

        Args:
            tenant_id: Tenant ID to delete

        Raises:
            TenantNotFoundError: If tenant does not exist
            TenancyError: If deletion fails

        Example:
            ```python
            await store.delete("tenant-123")
            ```
        """
        pass

    @abstractmethod
    async def list(
        self,
        skip: int = 0,
        limit: int = 100,
        status: TenantStatus | None = None,
    ) -> Iterable[Tenant]:
        """List tenants with pagination and optional filtering.

        Args:
            skip: Number of tenants to skip (for pagination)
            limit: Maximum number of tenants to return
            status: Optional filter by status

        Returns:
            List of tenants

        Example:
            ```python
            # Get first page
            tenants = await store.list(skip=0, limit=10)

            # Get second page
            tenants = await store.list(skip=10, limit=10)

            # Filter by status
            active = await store.list(status=TenantStatus.ACTIVE)
            ```
        """
        pass

    @abstractmethod
    async def count(self, status: TenantStatus | None = None) -> int:
        """Count tenants with optional filtering.

        Args:
            status: Optional filter by status

        Returns:
            Number of tenants matching criteria

        Example:
            ```python
            total = await store.count()
            active = await store.count(status=TenantStatus.ACTIVE)
            print(f"Active: {active}/{total}")
            ```
        """
        pass

    @abstractmethod
    async def exists(self, tenant_id: str) -> bool:
        """Check if tenant exists.

        This is more efficient than get_by_id() when you only need
        to check existence without retrieving the full object.

        Args:
            tenant_id: Tenant ID to check

        Returns:
            True if tenant exists, False otherwise

        Example:
            ```python
            if await store.exists("tenant-123"):
                print("Tenant exists")
            ```
        """
        pass

    @abstractmethod
    async def set_status(self, tenant_id: str, status: TenantStatus) -> Tenant:
        """Update tenant status.

        This is a convenience method for the common operation of
        changing tenant status (activate, suspend, etc.).

        Args:
            tenant_id: Tenant ID
            status: New status

        Returns:
            Updated tenant

        Raises:
            TenantNotFoundError: If tenant does not exist

        Example:
            ```python
            # Suspend tenant
            tenant = await store.set_status("123", TenantStatus.SUSPENDED)

            # Reactivate tenant
            tenant = await store.set_status("123", TenantStatus.ACTIVE)
            ```
        """
        pass

    @abstractmethod
    async def update_metadata(
        self,
        tenant_id: str,
        metadata: dict[str, Any],
    ) -> Tenant:
        """Update tenant metadata.

        Metadata is merged with existing metadata (not replaced).
        Use this for storing custom tenant properties.

        Args:
            tenant_id: Tenant ID
            metadata: Metadata to merge with existing metadata

        Returns:
            Updated tenant

        Raises:
            TenantNotFoundError: If tenant does not exist

        Example:
            ```python
            # Add/update metadata
            tenant = await store.update_metadata(
                "123",
                {
                    "plan": "enterprise",
                    "max_users": 100,
                    "features": ["sso", "audit_logs"]
                }
            )

            # Access metadata
            print(tenant.metadata["plan"])  # "enterprise"
            ```
        """
        pass

    async def get_by_ids(self, tenant_ids: Iterable[str]) -> Iterable[Tenant]:
        """Get multiple tenants by IDs (batch operation).

        Default implementation calls get_by_id() for each ID.
        Implementations should override for better performance.

        Args:
            tenant_ids: List of tenant IDs

        Returns:
            List of tenants (skips IDs that don't exist)

        Example:
            ```python
            ids = ["tenant-1", "tenant-2", "tenant-3"]
            tenants = await store.get_by_ids(ids)
            ```
        """
        tenants = []
        for tenant_id in tenant_ids:
            try:
                tenant = await self.get_by_id(tenant_id)
                tenants.append(tenant)
            except TenantNotFoundError:
                continue
        return tenants

    async def search(
        self,
        query: str,
        limit: int = 10,
        _scan_limit: int = 100,
    ) -> Iterable[Tenant]:
        """Search tenants by name or identifier.

        Default implementation performs an in-memory scan of the most recent
        ``_scan_limit`` tenants.  **Production stores should override this
        method** with a database-level query (e.g. ``WHERE identifier ILIKE
        :q OR name ILIKE :q``) to avoid loading all tenants into memory.

        Args:
            query:       Search string (case-insensitive substring match).
            limit:       Maximum number of results to return.
            _scan_limit: Maximum tenants fetched for in-memory scan.
                         Override this method to remove the limit entirely.

        Returns:
            List of matching :class:`~fastapi_tenancy.core.types.Tenant`
            objects, up to *limit* results.

        .. warning::
            The base implementation fetches up to ``_scan_limit`` records
            (default 100).  For deployments with thousands of tenants,
            override this method with a database-level search.

        Example:
            ```python
            results = await store.search("acme")
            for tenant in results:
                print(f"{tenant.identifier}: {tenant.name}")
            ```
        """
        query_lower = query.lower()
        all_tenants = await self.list(skip=0, limit=_scan_limit)
        matches = [
            t
            for t in all_tenants
            if query_lower in t.identifier.lower() or query_lower in t.name.lower()
        ]
        return matches[:limit]

    async def bulk_update_status(
        self,
        tenant_ids: Iterable[str],
        status: TenantStatus,
    ) -> Iterable[Tenant]:
        """Update status for multiple tenants (batch operation).

        Default implementation updates one at a time.
        Implementations should override for better performance.

        Args:
            tenant_ids: List of tenant IDs
            status: New status for all tenants

        Returns:
            List of updated tenants

        Example:
            ```python
            # Suspend multiple tenants
            ids = ["tenant-1", "tenant-2", "tenant-3"]
            updated = await store.bulk_update_status(ids, TenantStatus.SUSPENDED)
            ```
        """
        updated = []
        for tenant_id in tenant_ids:
            try:
                tenant = await self.set_status(tenant_id, status)
                updated.append(tenant)
            except TenantNotFoundError:
                continue
        return updated


__all__ = ["TenantStore"]
