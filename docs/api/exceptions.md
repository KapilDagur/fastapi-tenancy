# Exceptions

All exceptions in `fastapi-tenancy` extend `TenancyError`, which extends
`Exception`.  This lets callers catch either specific errors or the entire
family with a single `except TenancyError`.

## Hierarchy

```
TenancyError
├── ConfigurationError          # bad TenancyConfig at startup
├── TenantNotFoundError         # tenant ID / identifier has no match
├── TenantResolutionError       # could not extract tenant from request
├── TenantInactiveError         # tenant is suspended or deleted
├── IsolationError              # schema/database/RLS operation failed
├── MigrationError              # Alembic migration failure
├── RateLimitExceededError      # per-tenant rate limit hit (future)
├── TenantDataLeakageError      # cross-tenant data access detected
├── TenantQuotaExceededError    # plan quota exceeded (future)
└── DatabaseConnectionError     # cannot reach the database
```

## Auto-reference

::: fastapi_tenancy.core.exceptions
    options:
      show_source: true
      show_root_heading: true
      members_order: source
      filters: ["!^_"]

---

## Common patterns

### Catch a specific error

```python
from fastapi_tenancy import TenantNotFoundError

try:
    tenant = await store.get_by_identifier("unknown-corp")
except TenantNotFoundError as exc:
    # exc.tenant_id, exc.message, exc.details
    return {"error": "no such tenant", "id": exc.tenant_id}
```

### Catch the whole family

```python
from fastapi_tenancy import TenancyError

try:
    ...
except TenancyError as exc:
    logger.error("Tenancy subsystem error: %s | details=%s", exc.message, exc.details)
    raise HTTPException(500, "Internal error") from exc
```

### Access structured fields

Every `TenancyError` subclass exposes:

| Field | Type | Description |
|-------|------|-------------|
| `message` | `str` | Human-readable description |
| `details` | `dict` | Machine-readable structured data |
| `error_code` | `str` | Stable identifier for error type |

```python
except IsolationError as exc:
    print(exc.operation)  # "initialize_tenant"
    print(exc.tenant_id)  # "t-001"
    print(exc.details)    # {"schema": "tenant_acme", "error": "..."}
```

### In tests

```python
import pytest
from fastapi_tenancy import TenantNotFoundError

async def test_missing_tenant(store):
    with pytest.raises(TenantNotFoundError) as exc_info:
        await store.get_by_identifier("no-such-tenant")
    assert exc_info.value.details.get("identifier") == "no-such-tenant"
```
