"""Tenant resolution strategies.

This module provides different strategies for resolving the current tenant
from HTTP requests.

Available strategies:
- Header: Resolve from HTTP header (X-Tenant-ID)
- Subdomain: Resolve from subdomain (tenant.example.com)
- Path: Resolve from URL path (/tenants/{id}/resource)
- JWT: Resolve from JWT token claim

Example:
    ```python
    from fastapi_tenancy.resolution import HeaderTenantResolver

    resolver = HeaderTenantResolver(
        header_name="X-Tenant-ID",
        tenant_store=store,
    )

    tenant = await resolver.resolve(request)
    ```
"""

from fastapi_tenancy.resolution.base import BaseTenantResolver
from fastapi_tenancy.resolution.factory import ResolverFactory
from fastapi_tenancy.resolution.header import HeaderTenantResolver
from fastapi_tenancy.resolution.jwt import JWTTenantResolver
from fastapi_tenancy.resolution.path import PathTenantResolver
from fastapi_tenancy.resolution.subdomain import SubdomainTenantResolver

__all__ = [
    "BaseTenantResolver",
    "HeaderTenantResolver",
    "JWTTenantResolver",
    "PathTenantResolver",
    "ResolverFactory",
    "SubdomainTenantResolver",
]
