# Production Deployment

This guide covers connection pooling, security hardening, observability, and
operational best practices for running `fastapi-tenancy` in production.

## Connection pool sizing

### Schema and RLS strategies

Both strategies share a single database with a single connection pool.  Size
the pool based on expected concurrent requests:

```python
config = TenancyConfig(
    database_url="postgresql+asyncpg://...",
    # Rule of thumb: (num_workers × avg_concurrent_requests) + headroom
    database_pool_size=20,
    database_max_overflow=10,   # burst headroom
    database_pool_timeout=30,   # seconds to wait for a connection
    database_pool_recycle=1800, # recycle connections after 30 min
)
```

### Database-per-tenant strategy

Each tenant gets its own connection pool.  With hundreds of tenants this adds
up quickly.  Keep per-tenant pools small:

```python
config = TenancyConfig(
    ...
    isolation_strategy="database",
    database_pool_size=2,        # small per-tenant pool
    database_max_overflow=3,
)
```

Monitor the total open connections: `num_active_tenants × (pool_size + max_overflow)`.

### PostgreSQL max_connections

Ensure your PostgreSQL `max_connections` is not exhausted:

```sql
-- Check current connections
SELECT count(*) FROM pg_stat_activity WHERE state = 'active';

-- Show max
SHOW max_connections;
```

Use **PgBouncer** in transaction-pooling mode to reduce physical connections
when running many app instances.

---

## Security checklist

### SQL injection defence

`fastapi-tenancy` validates every tenant identifier before interpolating it
into DDL statements:

- `assert_safe_schema_name()` — rejects anything not matching `^[a-z][a-z0-9_]*$`
- `assert_safe_database_name()` — same rules for database names
- All interpolated identifiers are double-quoted: `"tenant_acme_corp"`

**Never bypass these checks** by building DDL strings yourself.

### JWT secret length

When using JWT resolution, the secret must be at least 32 characters:

```python
config = TenancyConfig(
    resolution_strategy="jwt",
    jwt_secret=os.environ["JWT_SECRET"],  # min 32 chars enforced at config time
)
```

Use a cryptographically random secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Error information disclosure

`TenancyMiddleware` never includes internal header names or stack traces in
error responses by default.  Error details are only included when
`debug_headers=True`.

### Tenant context isolation

`TenantContext` uses `contextvars.ContextVar` — each async task (HTTP
request) gets its own copy.  The `finally: TenantContext.clear()` in the
middleware ensures context is wiped even if the request handler raises.

Verify this with the concurrent isolation tests:

```bash
pytest tests/test_concurrent_isolation.py -v
```

---

## Environment variable configuration

Never hardcode credentials.  Use environment variables:

```bash
# .env (never commit this file)
TENANCY_DATABASE_URL=postgresql+asyncpg://app:secret@db.internal/production
TENANCY_RESOLUTION_STRATEGY=header
TENANCY_ISOLATION_STRATEGY=schema
TENANCY_DATABASE_POOL_SIZE=20
TENANCY_DATABASE_MAX_OVERFLOW=10
TENANCY_JWT_SECRET=your-48-char-random-secret-here
TENANCY_REDIS_URL=redis://cache.internal:6379/0
TENANCY_CACHE_ENABLED=true
```

```python
# settings.py — TenancyConfig reads from environment automatically
from fastapi_tenancy import TenancyConfig

config = TenancyConfig()  # reads all TENANCY_* env vars
```

---

## Health checks

Expose a `/health` endpoint that checks the tenancy subsystem:

```python
@app.get("/health")
async def health(request: Request):
    manager: TenancyManager = request.app.state.tenancy_manager
    health = await manager.health_check()
    status_code = 200 if health["status"] == "healthy" else 503
    return JSONResponse(content=health, status_code=status_code)
```

Make sure `/health` is in `skip_paths` (it is by default) so unauthenticated
load balancer probes don't fail tenant resolution.

---

## Observability

### Structured logging

`fastapi-tenancy` uses the standard Python `logging` module with `%s`-style
deferred formatting (never f-strings in log calls):

```python
# Configure in your app startup
import logging
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", '
           '"logger": "%(name)s", "message": %(message)s}',
)

# Set library verbosity
logging.getLogger("fastapi_tenancy").setLevel(logging.WARNING)
logging.getLogger("fastapi_tenancy.middleware").setLevel(logging.INFO)
```

### Request tracing

Add tenant ID to your tracing spans:

```python
from opentelemetry import trace
from fastapi_tenancy import TenantContext

@app.middleware("http")
async def add_tenant_to_trace(request: Request, call_next):
    response = await call_next(request)
    tenant = TenantContext.get_optional()
    if tenant:
        span = trace.get_current_span()
        span.set_attribute("tenant.id", tenant.id)
        span.set_attribute("tenant.identifier", tenant.identifier)
    return response
```

### Metrics

Use the `manager.get_metrics()` method to expose tenant counts:

```python
from prometheus_client import Gauge

tenant_count = Gauge("tenancy_tenant_count", "Total tenants", ["status"])

@app.on_event("startup")
async def setup_metrics():
    async def update():
        metrics = await manager.get_metrics()
        tenant_count.labels(status="active").set(metrics["active_tenants"])
        tenant_count.labels(status="suspended").set(metrics["suspended_tenants"])
    # schedule update() on a timer
```

---

## Multi-worker deployments

### Uvicorn + Gunicorn

```bash
gunicorn myapp.main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000
```

Each worker process runs its own `TenancyManager` and connection pool.  This
is correct — `TenancyManager` is designed to be per-process.

### Redis cache in multi-worker setups

With multiple workers, the `RedisTenantStore` cache ensures all workers see
consistent tenant data.  Configure a shared Redis instance:

```python
from fastapi_tenancy import TenancyConfig
from fastapi_tenancy.storage.postgres import SQLAlchemyTenantStore
from fastapi_tenancy.storage.redis import RedisTenantStore

primary = SQLAlchemyTenantStore(database_url=os.environ["DATABASE_URL"])
store = RedisTenantStore(
    redis_url=os.environ["REDIS_URL"],
    primary_store=primary,
    ttl=300,  # 5-minute cache TTL
)

config = TenancyConfig(...)
app = FastAPI(
    lifespan=TenancyManager.create_lifespan(config, tenant_store=store)
)
```

---

## Kubernetes deployment

```yaml
# k8s/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: fastapi-tenancy-app
spec:
  replicas: 3
  template:
    spec:
      containers:
        - name: app
          image: myapp:latest
          env:
            - name: TENANCY_DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: db-secret
                  key: url
            - name: TENANCY_RESOLUTION_STRATEGY
              value: header
            - name: TENANCY_ISOLATION_STRATEGY
              value: schema
            - name: TENANCY_DATABASE_POOL_SIZE
              value: "10"
          livenessProbe:
            httpGet:
              path: /health
              port: 8000
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
```

---

## Production readiness checklist

```
Infrastructure
  ✓ PostgreSQL with adequate max_connections (or PgBouncer)
  ✓ Redis for tenant cache (multi-worker deployments)
  ✓ Environment variables for all secrets — no hardcoded credentials
  ✓ /health endpoint in skip_paths and exposed to load balancer

Security
  ✓ JWT secret ≥ 32 chars, cryptographically random
  ✓ debug_headers=False in production
  ✓ TLS on all database and Redis connections
  ✓ Principle of least privilege on DB user (no SUPERUSER)

Observability
  ✓ Structured JSON logging configured
  ✓ Log level WARN for fastapi_tenancy, INFO for middleware
  ✓ /health endpoint wired to manager.health_check()
  ✓ Tenant ID propagated to tracing spans

Operations
  ✓ Alembic migrations tested against a copy of production schema
  ✓ destroy_tenant called with metadata= or table_names= (not left as default)
  ✓ Concurrent isolation test suite passing (pytest tests/test_concurrent_isolation.py)
  ✓ py.typed verified: mypy --strict your_app.py passes
```
