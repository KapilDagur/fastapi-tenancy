"""In-memory tenant storage implementation for testing and development.

WARNING: This implementation stores data in memory only. All data is lost
when the process restarts. Use ONLY for testing and development.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi_tenancy.core.exceptions import TenantNotFoundError
from fastapi_tenancy.storage.tenant_store import TenantStore

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastapi_tenancy.core.types import Tenant, TenantStatus

logger = logging.getLogger(__name__)


class InMemoryTenantStore(TenantStore):
    """In-memory tenant storage for testing and development.

    This implementation stores all tenant data in memory using Python
    dictionaries. It's fast and simple but NOT persistent.

    Use cases:
    - Unit testing
    - Integration testing
    - Local development
    - Demos and examples

    DO NOT USE IN PRODUCTION - all data is lost on restart!

    Example:
        ```python
        # Create store
        store = InMemoryTenantStore()

        # Create test tenants
        tenant1 = Tenant(id="1", identifier="test1", name="Test 1")
        await store.create(tenant1)

        tenant2 = Tenant(id="2", identifier="test2", name="Test 2")
        await store.create(tenant2)

        # Query
        tenant = await store.get_by_id("1")
        tenants = await store.list()

        # Cleanup (for testing)
        store.clear()
        ```

    Attributes:
        _tenants: Dictionary mapping tenant ID to Tenant
        _identifier_map: Dictionary mapping identifier to tenant ID
    """

    def __init__(self) -> None:
        """Initialize in-memory tenant store.

        Example:
            ```python
            store = InMemoryTenantStore()
            ```
        """
        self._tenants: dict[str, Tenant] = {}
        self._identifier_map: dict[str, str] = {}
        logger.info("Initialized in-memory tenant store")

    async def get_by_id(self, tenant_id: str) -> Tenant:
        """Get tenant by ID.

        Args:
            tenant_id: Tenant ID

        Returns:
            Tenant object

        Raises:
            TenantNotFoundError: If tenant does not exist
        """
        if tenant_id not in self._tenants:
            logger.warning("Tenant not found: %s", tenant_id)
            raise TenantNotFoundError(identifier=tenant_id)

        logger.debug("Retrieved tenant: %s", tenant_id)
        return self._tenants[tenant_id]

    async def get_by_identifier(self, identifier: str) -> Tenant:
        """Get tenant by identifier.

        Args:
            identifier: Tenant identifier

        Returns:
            Tenant object

        Raises:
            TenantNotFoundError: If tenant does not exist
        """
        if identifier not in self._identifier_map:
            logger.warning("Tenant not found by identifier: %s", identifier)
            raise TenantNotFoundError(identifier=identifier)

        tenant_id = self._identifier_map[identifier]
        return self._tenants[tenant_id]

    async def create(self, tenant: Tenant) -> Tenant:
        """Create a new tenant.

        Args:
            tenant: Tenant to create

        Returns:
            Created tenant

        Raises:
            ValueError: If tenant already exists
        """
        if tenant.id in self._tenants:
            raise ValueError(f"Tenant with ID {tenant.id} already exists")

        if tenant.identifier in self._identifier_map:
            raise ValueError(
                f"Tenant with identifier {tenant.identifier} already exists"
            )

        # Store tenant
        self._tenants[tenant.id] = tenant
        self._identifier_map[tenant.identifier] = tenant.id

        logger.info("Created tenant: %s (%s)", tenant.id, tenant.identifier)
        return tenant

    async def update(self, tenant: Tenant) -> Tenant:
        """Update an existing tenant.

        Args:
            tenant: Tenant with updated values

        Returns:
            Updated tenant

        Raises:
            TenantNotFoundError: If tenant does not exist
        """
        if tenant.id not in self._tenants:
            raise TenantNotFoundError(identifier=tenant.id)

        # Update identifier mapping if changed
        old_tenant = self._tenants[tenant.id]
        if old_tenant.identifier != tenant.identifier:
            # Remove old mapping
            del self._identifier_map[old_tenant.identifier]
            # Add new mapping
            self._identifier_map[tenant.identifier] = tenant.id

        # Create updated tenant with new timestamp
        updated_tenant = tenant.model_copy(
            update={"updated_at": datetime.now(UTC)}
        )
        self._tenants[tenant.id] = updated_tenant

        logger.info("Updated tenant: %s", tenant.id)
        return updated_tenant

    async def delete(self, tenant_id: str) -> None:
        """Delete a tenant.

        Args:
            tenant_id: Tenant ID to delete

        Raises:
            TenantNotFoundError: If tenant does not exist
        """
        if tenant_id not in self._tenants:
            raise TenantNotFoundError(identifier=tenant_id)

        tenant = self._tenants[tenant_id]

        # Remove from both mappings
        del self._identifier_map[tenant.identifier]
        del self._tenants[tenant_id]

        logger.info("Deleted tenant: %s", tenant_id)

    async def list(
        self,
        skip: int = 0,
        limit: int = 100,
        status: TenantStatus | None = None,
    ) -> list[Tenant]:
        """List tenants with pagination.

        Args:
            skip: Number to skip
            limit: Maximum to return
            status: Optional status filter

        Returns:
            List of tenants
        """
        tenants = list(self._tenants.values())

        # Filter by status if provided
        if status:
            tenants = [t for t in tenants if t.status == status]

        # Sort by created_at descending (newest first)
        tenants.sort(key=lambda t: t.created_at, reverse=True)

        # Apply pagination
        result = tenants[skip : skip + limit]

        logger.debug("Listed %d tenants (skip=%d limit=%d total=%d)", len(result), skip, limit, len(self._tenants))  # noqa: E501
        return result

    async def count(self, status: TenantStatus | None = None) -> int:
        """Count tenants.

        Args:
            status: Optional status filter

        Returns:
            Number of tenants
        """
        if status is None:
            count = len(self._tenants)
        else:
            count = sum(1 for t in self._tenants.values() if t.status == status)

        logger.debug("Counted %d tenants", count)
        return count

    async def exists(self, tenant_id: str) -> bool:
        """Check if tenant exists.

        Args:
            tenant_id: Tenant ID

        Returns:
            True if exists
        """
        exists = tenant_id in self._tenants
        logger.debug("Tenant %s exists: %s", tenant_id, exists)
        return exists

    async def set_status(self, tenant_id: str, status: TenantStatus) -> Tenant:
        """Update tenant status.

        Args:
            tenant_id: Tenant ID
            status: New status

        Returns:
            Updated tenant

        Raises:
            TenantNotFoundError: If tenant does not exist
        """
        tenant = await self.get_by_id(tenant_id)

        updated_tenant = tenant.model_copy(
            update={
                "status": status,
                "updated_at": datetime.now(UTC),
            }
        )

        self._tenants[tenant_id] = updated_tenant

        logger.info("Updated tenant %s status to %s", tenant_id, status.value)
        return updated_tenant

    async def update_metadata(
        self,
        tenant_id: str,
        metadata: dict[str, Any],
    ) -> Tenant:
        """Update tenant metadata.

        Args:
            tenant_id: Tenant ID
            metadata: Metadata to merge

        Returns:
            Updated tenant

        Raises:
            TenantNotFoundError: If tenant does not exist
        """
        tenant = await self.get_by_id(tenant_id)

        # Merge metadata
        updated_metadata = {**tenant.metadata, **metadata}

        updated_tenant = tenant.model_copy(
            update={
                "metadata": updated_metadata,
                "updated_at": datetime.now(UTC),
            }
        )

        self._tenants[tenant_id] = updated_tenant

        logger.info("Updated metadata for tenant: %s", tenant_id)
        return updated_tenant

    async def get_by_ids(self, tenant_ids: Iterable[str]) -> Iterable[Tenant]:
        """Get multiple tenants by IDs (optimized batch operation).

        Args:
            tenant_ids: List of tenant IDs

        Returns:
            List of tenants (skips IDs that don't exist)
        """
        tenants = []
        for tenant_id in tenant_ids:
            if tenant_id in self._tenants:
                tenants.append(self._tenants[tenant_id])

        logger.debug("Retrieved %d tenants by IDs", len(tenants))
        return tenants

    async def search( # type: ignore
        self,
        query: str,
        limit: int = 10,
    ) -> Iterable[Tenant]:
        """Search tenants by name or identifier.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            List of matching tenants
        """
        query_lower = query.lower()
        matches = [
            t
            for t in self._tenants.values()
            if query_lower in t.identifier.lower() or query_lower in t.name.lower()
        ]

        # Sort by relevance (exact matches first)
        matches.sort(
            key=lambda t: (
                t.identifier.lower() == query_lower,
                t.identifier.lower().startswith(query_lower),
                query_lower in t.name.lower(),
            ),
            reverse=True,
        )

        result = matches[:limit]
        logger.debug("Search %r returned %d results", query, len(result))
        return result

    async def bulk_update_status(
        self,
        tenant_ids: Iterable[str],
        status: TenantStatus,
    ) -> Iterable[Tenant]:
        """Update status for multiple tenants (optimized batch operation).

        Args:
            tenant_ids: List of tenant IDs
            status: New status for all tenants

        Returns:
            List of updated tenants
        """
        updated = []
        timestamp = datetime.now(UTC)

        for tenant_id in tenant_ids:
            if tenant_id in self._tenants:
                tenant = self._tenants[tenant_id]
                updated_tenant = tenant.model_copy(
                    update={
                        "status": status,
                        "updated_at": timestamp,
                    }
                )
                self._tenants[tenant_id] = updated_tenant
                updated.append(updated_tenant)

        logger.info("Bulk updated %d tenants to status %s", len(updated), status.value)
        return updated

    def clear(self) -> None:
        """Clear all tenants (for testing).

        This removes all tenants from memory. Use only in tests!

        Example:
            ```python
            @pytest.fixture
            async def store():
                store = InMemoryTenantStore()
                yield store
                store.clear()  # Cleanup after test
            ```
        """
        self._tenants.clear()
        self._identifier_map.clear()
        logger.info("Cleared all tenants from memory")

    def get_all_tenants(self) -> dict[str, Tenant]:
        """Get all tenants as dictionary (for testing/debugging).

        Returns:
            Dictionary mapping tenant ID to Tenant

        Example:
            ```python
            all_tenants = store.get_all_tenants()
            print(f"Total tenants: {len(all_tenants)}")
            ```
        """
        return self._tenants.copy()

    def get_statistics(self) -> dict[str, Any]:
        """Get store statistics (for monitoring).

        Returns:
            Dictionary with statistics

        Example:
            ```python
            stats = store.get_statistics()
            print(f"Total: {stats['total']}")
            print(f"Active: {stats['by_status']['active']}")
            ```
        """
        by_status:dict[str, Any] = {}
        for tenant in self._tenants.values():
            status = tenant.status.value
            by_status[status] = by_status.get(status, 0) + 1

        return {
            "total": len(self._tenants),
            "by_status": by_status,
            "identifiers_mapped": len(self._identifier_map),
        }


__all__ = ["InMemoryTenantStore"]
