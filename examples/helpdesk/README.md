# Helpdesk — a complete multi-tenant API

A working B2B support desk built on `fastapi-tenancy`. Each customer
organisation is a tenant with its own PostgreSQL schema; tickets never cross
the boundary.

This is a **standalone uv project**, deliberately not part of the library's own
package or test suite. It depends on `fastapi-tenancy` the way a real
application does — through its own lockfile and the published `[postgres]`
extra — so it doubles as a consumer-side integration test. Nothing here adds a
dependency to the library.

## What it demonstrates

| Concern | How |
|---|---|
| **Isolation** | `IsolationStrategy.SCHEMA` — one PostgreSQL schema per tenant. The models carry **no `tenant_id` column**: the schema boundary *is* the isolation, so a handler cannot leak by forgetting a `WHERE` clause. |
| **Resolution** | `X-Tenant-ID` header. Switching to subdomain or JWT is a config change, not a code change. |
| **Wiring** | The manager is built once in `create_app()` and captured by the dependency factories — no `app.state` lookups, no startup-order dependency. |
| **Excluded paths** | `/health` and `/admin` bypass tenancy. Matching is segment-anchored, so `/admin` does not also exempt `/administrator`. |
| **Caching** | `l1_cache_enabled=True` — the in-process LRU, no Redis required. |
| **Provisioning** | `register_tenant(app_metadata=Base.metadata)` creates the schema *and* its tables, and rolls the row back if provisioning fails. |

## Layout

```
src/helpdesk/
  app.py        # create_app() — routes, lifespan, middleware
  models.py     # per-tenant SQLAlchemy models (no tenant_id anywhere)
  schemas.py    # request/response models
tests/
  conftest.py       # Testcontainers PostgreSQL + lifespan-managed client
  test_crud.py      # full CRUD over tickets
  test_isolation.py # the cross-tenant guarantees
```

## Running it

```bash
docker run --rm -e POSTGRES_PASSWORD=pw -p 5432:5432 postgres:16-alpine

uv sync --all-groups
export HELPDESK_DATABASE_URL="postgresql+asyncpg://postgres:pw@localhost/postgres"
uv run uvicorn helpdesk.app:app --reload
```

Provision a tenant and open a ticket:

```bash
curl -XPOST localhost:8000/admin/tenants \
     -H 'content-type: application/json' \
     -d '{"identifier":"acme-corp","name":"Acme","plan":"pro"}'

curl -XPOST localhost:8000/tickets -H 'X-Tenant-ID: acme-corp' \
     -H 'content-type: application/json' \
     -d '{"subject":"Printer on fire","requester_email":"ops@acme.test"}'

curl localhost:8000/tickets -H 'X-Tenant-ID: acme-corp'
```

Interactive docs: <http://localhost:8000/docs>.

## API

| Method | Path | Tenant header | Notes |
|---|---|---|---|
| `GET` | `/health` | no | liveness; excluded from tenancy |
| `POST` | `/admin/tenants` | no | provision an organisation + its schema |
| `GET` | `/admin/tenants` | no | list all tenants |
| `POST` | `/admin/tenants/{identifier}/suspend` | no | suspend; its requests then 403 |
| `GET` | `/me` | yes | caller's tenant and resolved quota |
| `POST` | `/tickets` | yes | create |
| `GET` | `/tickets` | yes | list, `?ticket_status=` filter |
| `GET` | `/tickets/{id}` | yes | read |
| `PATCH` | `/tickets/{id}` | yes | partial update |
| `DELETE` | `/tickets/{id}` | yes | delete |

## QA gate

```bash
make qa      # lint + format + type + test
```

Or individually:

```bash
uv run ruff check .            # lint
uv run ruff format --check .   # format
uv run mypy src tests          # type
uv run pytest                  # test
```

The tests start a real PostgreSQL container via Testcontainers, so **Docker
must be running**; they skip themselves if it is not. That is not
gold-plating: under SQLite the SCHEMA provider falls back to table-name
prefixing, so a SQLite suite would exercise a *different* isolation mechanism
than the one this example ships — and proving the isolation is the point.

`conftest.py` drives startup with `asgi-lifespan`. httpx's `ASGITransport`
alone does **not** run lifespan events, which silently leaves the tenant store
uninitialised and every request failing on a missing `tenants` table.

## Things this example is not

- **The `/admin` routes have no authentication.** They provision and suspend
  tenants platform-wide. Put them behind operator-only authn/authz, a separate
  port, or a private network before using this shape.
- **No migrations.** Tables are created at provisioning time from
  `Base.metadata`. A real deployment should use `TenantMigrationManager` so
  schema changes reach existing tenants — creating tables only works for
  tenants provisioned *after* the model changed.
- **No rate limiting.** It needs Redis, so it is off. `build_config()` shows
  the three settings to enable it, including `rate_limit_fail_closed`.
