# TenancyMiddleware

`TenancyMiddleware` is a Starlette `BaseHTTPMiddleware` that runs on every
request.  It resolves the tenant, validates it is active, sets
`TenantContext`, and clears it in a `finally` block.

## Auto-reference

::: fastapi_tenancy.middleware.tenancy.TenancyMiddleware
    options:
      show_source: true
      show_root_heading: true
      members_order: source
      filters: ["!^_is_path", "!^_should", "!^_error"]

---

## Request processing pipeline

```
Request
  │
  ▼
_should_skip_request(request)
  │  OPTIONS → skip
  │  path in skip_paths → skip
  │
  ▼
resolver.resolve(request)
  │  TenantNotFoundError → 404
  │  TenantResolutionError → 400
  │
  ▼
tenant.is_active()
  │  inactive → 403
  │
  ▼
TenantContext.set(tenant)
request.state.tenant = tenant
  │
  ▼
call_next(request)
  │
  ▼
[optional] debug response headers
  │
  ▼
Response
  │
  finally: TenantContext.clear()   ← always runs, even on exception
```

## Skip paths

By default the following prefixes are excluded from tenant resolution:

```
/health
/metrics
/docs
/redoc
/openapi.json
/favicon.ico
```

Customise via `skip_paths=`:

```python
TenancyManager.create_lifespan(
    config,
    skip_paths=["/health", "/internal", "/_admin"],
)
```

## Skip-path helpers

Two helpers are available for testing:

| Method | When to use |
|--------|-------------|
| `_is_path_skipped(path: str) → bool` | Path-only check; useful in unit tests |
| `_should_skip_request(request) → bool` | Full check (OPTIONS + path); used internally |

```python
middleware = TenancyMiddleware(app, config=config)
assert middleware._is_path_skipped("/health")        # True
assert not middleware._is_path_skipped("/api/users") # False
```

## Debug headers

When `debug_headers=True`, every response gets:

```
X-Tenant-ID: t-001
X-Tenant-Identifier: acme-corp
```

Also enables detailed error bodies in 5xx responses.  **Never enable in
production.**

```python
TenancyManager.create_lifespan(config, debug_headers=True)
```

## Error responses

| Condition | HTTP | `error` field |
|-----------|------|---------------|
| Header / subdomain / path not found | 400 | `tenant_resolution_failed` |
| Tenant not found in store | 404 | `tenant_not_found` |
| Tenant suspended or deleted | 403 | `tenant_inactive` |
| No resolver configured | 503 | `service_unavailable` |
| Unexpected exception | 500 | `internal_error` |

```json
{
  "error": "tenant_not_found",
  "message": "Tenant 'acme-corp' does not exist",
  "details": {"identifier": "acme-corp"}
}
```
