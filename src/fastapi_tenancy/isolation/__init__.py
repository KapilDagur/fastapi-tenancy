"""Data isolation strategies.

This module provides different strategies for isolating tenant data.

Available strategies:
- Schema: Separate PostgreSQL schema per tenant
- Database: Separate database per tenant
- RLS: Row-Level Security policies
- Hybrid: Mix strategies based on tenant tier

Example:
    ```python
    from fastapi_tenancy.isolation import SchemaIsolationProvider

    provider = SchemaIsolationProvider(config)

    async with provider.get_session(tenant) as session:
        users = await session.execute(select(User))
    ```
"""

from fastapi_tenancy.isolation.base import BaseIsolationProvider
from fastapi_tenancy.isolation.database import DatabaseIsolationProvider
from fastapi_tenancy.isolation.factory import IsolationProviderFactory
from fastapi_tenancy.isolation.hybrid import HybridIsolationProvider
from fastapi_tenancy.isolation.rls import RLSIsolationProvider
from fastapi_tenancy.isolation.schema import SchemaIsolationProvider

__all__ = [
    "BaseIsolationProvider",
    "DatabaseIsolationProvider",
    "HybridIsolationProvider",
    "IsolationProviderFactory",
    "RLSIsolationProvider",
    "SchemaIsolationProvider",
]
