# TenancyConfig

`TenancyConfig` is the single configuration object for the entire library.
It is a [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
model — every field can be set via environment variable or keyword argument.

## Auto-reference

::: fastapi_tenancy.core.config.TenancyConfig
    options:
      show_source: true
      show_root_heading: true
      members_order: source
      filters: ["!^_"]

---

## Environment variable mapping

All fields can be configured via environment variables prefixed with `TENANCY_`:

```bash
TENANCY_DATABASE_URL=postgresql+asyncpg://user:pass@host/db
TENANCY_RESOLUTION_STRATEGY=header
TENANCY_ISOLATION_STRATEGY=schema
TENANCY_TENANT_HEADER_NAME=X-Tenant-ID
TENANCY_DOMAIN_SUFFIX=.myapp.io
TENANCY_JWT_SECRET=your-secret-min-32-chars
TENANCY_DATABASE_POOL_SIZE=10
TENANCY_DATABASE_ECHO=false
TENANCY_CACHE_ENABLED=false
TENANCY_REDIS_URL=redis://localhost:6379/0
```

## Validation

`TenancyConfig.validate_configuration()` is called automatically in
`model_post_init` to enforce cross-field constraints:

| Constraint | Error |
|------------|-------|
| `resolution_strategy=jwt` without `jwt_secret` | `ConfigurationError` |
| `resolution_strategy=subdomain` without `domain_suffix` | `ConfigurationError` |
| `cache_enabled=True` without `redis_url` | `ConfigurationError` |
| `isolation_strategy=hybrid` without `premium_tenants` | `ConfigurationError` |

## Quick reference

```python
from fastapi_tenancy import TenancyConfig

# Minimal — header resolution + schema isolation
config = TenancyConfig(
    database_url="postgresql+asyncpg://user:pass@localhost/myapp",
    resolution_strategy="header",
    isolation_strategy="schema",
)

# JWT resolution
config = TenancyConfig(
    database_url="postgresql+asyncpg://...",
    resolution_strategy="jwt",
    jwt_secret="at-least-32-characters-long-secret",
    jwt_algorithm="HS256",
    jwt_tenant_claim="tenant_id",
    isolation_strategy="schema",
)

# Subdomain resolution
config = TenancyConfig(
    database_url="postgresql+asyncpg://...",
    resolution_strategy="subdomain",
    domain_suffix=".myapp.io",
    isolation_strategy="schema",
)

# Hybrid isolation — schema for premium, RLS for standard
config = TenancyConfig(
    database_url="postgresql+asyncpg://...",
    resolution_strategy="header",
    isolation_strategy="hybrid",
    premium_isolation_strategy="schema",
    standard_isolation_strategy="rls",
    premium_tenants=["acme-corp", "widgets-inc"],
)

# Redis cache
config = TenancyConfig(
    database_url="postgresql+asyncpg://...",
    resolution_strategy="header",
    isolation_strategy="rls",
    cache_enabled=True,
    redis_url="redis://localhost:6379/0",
)
```
