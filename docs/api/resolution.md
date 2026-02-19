# Resolution

Resolvers extract the tenant identifier from each HTTP request.

## Class hierarchy

```
BaseTenantResolver (ABC)
├── HeaderTenantResolver      # X-Tenant-ID: acme-corp
├── SubdomainTenantResolver   # acme-corp.myapp.io
├── PathTenantResolver        # /tenants/acme-corp/users
└── JWTTenantResolver         # Authorization: Bearer <token with tenant_id claim>
```

## Strategy comparison

| Strategy | Best for | Requires |
|----------|----------|----------|
| Header | APIs, mobile apps, server-to-server | Client sends header |
| Subdomain | Consumer SaaS, white-label | Wildcard DNS + TLS |
| Path | REST APIs, multi-tenant admin UIs | URL convention |
| JWT | Stateless auth, token-based tenancy | JWT infrastructure |

## BaseTenantResolver

::: fastapi_tenancy.resolution.base.BaseTenantResolver
    options:
      show_source: true
      show_root_heading: true
      members_order: source
      filters: ["!^_"]

---

## HeaderTenantResolver

::: fastapi_tenancy.resolution.header.HeaderTenantResolver
    options:
      show_source: false
      show_root_heading: true
      members_order: source
      filters: ["!^_"]

### Security note — error details

`HeaderTenantResolver` never includes the list of available request headers
in error responses.  Doing so would expose internal infrastructure headers
(like forwarded-for addresses, auth tokens, or custom proxy headers) to
untrusted callers.  Only the *expected* header name is included in error
details.

---

## SubdomainTenantResolver

::: fastapi_tenancy.resolution.subdomain.SubdomainTenantResolver
    options:
      show_source: false
      show_root_heading: true
      members_order: source
      filters: ["!^_"]

---

## PathTenantResolver

::: fastapi_tenancy.resolution.path.PathTenantResolver
    options:
      show_source: false
      show_root_heading: true
      members_order: source
      filters: ["!^_"]

---

## JWTTenantResolver

::: fastapi_tenancy.resolution.jwt.JWTTenantResolver
    options:
      show_source: false
      show_root_heading: true
      members_order: source
      filters: ["!^_"]

---

## ResolverFactory

::: fastapi_tenancy.resolution.factory.ResolverFactory
    options:
      show_source: false
      show_root_heading: true

---

## Implementing a custom resolver

```python
from fastapi import Request
from fastapi_tenancy import BaseTenantResolver, Tenant, TenantResolutionError
from fastapi_tenancy.storage.tenant_store import TenantStore

class ApiKeyTenantResolver(BaseTenantResolver):
    """Resolve tenant from X-API-Key header via a key→tenant lookup table."""

    def __init__(self, api_key_store: dict[str, str],
                 tenant_store: TenantStore) -> None:
        super().__init__(tenant_store)
        self._keys = api_key_store  # {"apikey123": "acme-corp", ...}

    async def resolve(self, request: Request) -> Tenant:
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            raise TenantResolutionError(
                reason="X-API-Key header is required",
                strategy="api-key",
            )
        tenant_id = self._keys.get(api_key)
        if not tenant_id:
            raise TenantResolutionError(
                reason="Invalid API key",
                strategy="api-key",
            )
        return await self.get_tenant_by_identifier(tenant_id)
```

Pass it to the manager:

```python
resolver = ApiKeyTenantResolver(api_key_store, tenant_store)
app = FastAPI(
    lifespan=TenancyManager.create_lifespan(config, resolver=resolver)
)
```
