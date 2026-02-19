# Resolution Strategies

Resolution strategies determine **how the tenant is identified from each HTTP request**.
The resolver runs inside `TenancyMiddleware` before any route handler sees the request.

---

## Header resolution (default)

Reads a tenant identifier from an HTTP header.

```python
config = TenancyConfig(
    database_url="...",
    resolution_strategy="header",
    tenant_header_name="X-Tenant-ID",   # default
)
```

**Request example:**
```http
GET /api/users HTTP/1.1
Host: api.example.com
X-Tenant-ID: acme-corp
Authorization: Bearer token123
```

**Security**: header names are never leaked in error responses. Only
`expected_header` is included — the full header map is never exposed.

---

## Subdomain resolution

Extracts the tenant slug from the request hostname.

```python
config = TenancyConfig(
    database_url="...",
    resolution_strategy="subdomain",
    domain_suffix=".example.com",   # required
)
```

**Request examples:**
```
https://acme-corp.example.com/dashboard    → tenant: acme-corp
https://widgets-inc.example.com/api/users  → tenant: widgets-inc
```

Multi-level subdomains use the rightmost part before the domain:
```
https://app.acme-corp.example.com/  → tenant: acme-corp
```

**Requirements:** wildcard DNS (`*.example.com`), SSL wildcard certificate.

---

## JWT resolution

Extracts the tenant identifier from a claim in a JWT Bearer token.

```python
config = TenancyConfig(
    database_url="...",
    resolution_strategy="jwt",
    jwt_secret="your-secret-at-least-32-chars-long",
    jwt_algorithm="HS256",
    jwt_tenant_claim="tenant_id",   # claim name in JWT payload
)
```

**Request example:**
```http
GET /api/orders HTTP/1.1
Authorization: Bearer eyJhbGciOiJIUzI1NiJ9...
```

**JWT payload example:**
```json
{
  "sub": "user-123",
  "tenant_id": "acme-corp",
  "exp": 1734567890
}
```

!!! tip "jwt_secret minimum length"
    The `jwt_secret` must be at least 32 characters. This is validated at
    config construction time.

---

## Path resolution

Extracts the tenant slug from the URL path.

```python
config = TenancyConfig(
    database_url="...",
    resolution_strategy="path",
    path_prefix="/tenants",   # default
)
```

**Request examples:**
```
GET /tenants/acme-corp/users        → tenant: acme-corp
GET /tenants/widgets-inc/dashboard  → tenant: widgets-inc
```

---

## Custom resolver

Implement `BaseTenantResolver` to create a fully custom strategy.

```python
from fastapi import Request
from fastapi_tenancy import (
    BaseTenantResolver,   # exported from top-level package
    Tenant,
    TenantResolutionError,
)
from fastapi_tenancy.storage.tenant_store import TenantStore


class ApiKeyTenantResolver(BaseTenantResolver):
    """Resolve tenant from an API key stored in a database."""

    def __init__(self, tenant_store: TenantStore, api_keys: dict[str, str]) -> None:
        super().__init__(tenant_store)
        # api_keys: {api_key: tenant_identifier}
        self._api_keys = api_keys

    async def resolve(self, request: Request) -> Tenant:
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            raise TenantResolutionError(
                reason="X-API-Key header is required",
                strategy="api_key",
            )

        tenant_id = self._api_keys.get(api_key)
        if not tenant_id:
            raise TenantResolutionError(
                reason="Invalid API key",
                strategy="api_key",
                details={"hint": "Contact support to obtain a valid API key"},
            )

        return await self.get_tenant_by_identifier(tenant_id)


# Wire it up:
from fastapi_tenancy.storage.memory import InMemoryTenantStore

store = InMemoryTenantStore()
resolver = ApiKeyTenantResolver(
    tenant_store=store,
    api_keys={"sk-prod-abc123": "acme-corp"},
)

manager = TenancyManager(config, resolver=resolver)
```

### Protocol vs ABC

`BaseTenantResolver` is an **ABC** (Abstract Base Class) — inherit from it
for the full base implementation (logging, `validate_tenant_identifier`,
`get_tenant_by_identifier`).

`TenantResolver` is a **Protocol** — implement it structurally without
inheriting, useful for third-party integrations:

```python
from fastapi_tenancy.core.types import TenantResolver

class MyResolver:
    """Structurally compatible with TenantResolver protocol."""
    async def resolve(self, request: Any) -> Tenant: ...

# Runtime check
assert isinstance(MyResolver(), TenantResolver)  # True
```
