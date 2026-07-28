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
| **Excluded paths** | `/health`, `/admin` and `/panel` bypass tenancy. Matching is segment-anchored, so `/admin` does not also exempt `/administrator`. |
| **Caching** | `l1_cache_enabled=True` — the in-process LRU, no Redis required. |
| **Provisioning** | `register_tenant(app_metadata=Base.metadata)` creates the schema *and* its tables, and rolls the row back if provisioning fails. |
| **AuthN** | `fastapi-users` with a **per-tenant** user table and a database token strategy — so tokens can be revoked, and a token issued for one customer is meaningless against another. |
| **AuthZ** | `agent` / `admin` roles *within* a tenant. Deleting a ticket needs admin; the tenant boundary itself is the schema, so no role can cross it. |
| **Operator panel** | `sqladmin` at `/panel`, session-authenticated, managing the tenant registry. |

## Layout

```
src/helpdesk/
  app.py        # create_app() — routes, lifespan, middleware, router wiring
  auth.py       # fastapi-users wiring on the tenant-scoped session
  admin.py      # sqladmin operator panel over the tenant registry
  models.py     # per-tenant models: User, AccessToken, Ticket (no tenant_id)
  schemas.py    # request/response models
tests/
  conftest.py          # Testcontainers PostgreSQL + lifespan-managed client
  test_crud.py         # full CRUD over tickets
  test_isolation.py    # the cross-tenant guarantees
  test_auth.py         # registration, login, roles, cross-tenant tokens
  test_admin_panel.py  # operator panel access and safety
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

# Register a user in that tenant and log in.
curl -XPOST localhost:8000/auth/register -H 'X-Tenant-ID: acme-corp' \
     -H 'content-type: application/json' \
     -d '{"email":"agent@acme.example.com","password":"correct-horse-battery-staple"}'

TOKEN=$(curl -s -XPOST localhost:8000/auth/login -H 'X-Tenant-ID: acme-corp' \
     -d 'username=agent@acme.example.com&password=correct-horse-battery-staple' \
     | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl -XPOST localhost:8000/tickets -H "X-Tenant-ID: acme-corp" \
     -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
     -d '{"subject":"Printer on fire","requester_email":"ops@acme.test"}'

curl localhost:8000/tickets -H "X-Tenant-ID: acme-corp" -H "Authorization: Bearer $TOKEN"
```

Interactive docs: <http://localhost:8000/docs>.
Operator panel: <http://localhost:8000/panel> (`operator` / `operator-dev-password`
by default — override with `HELPDESK_OPERATOR_USER` / `HELPDESK_OPERATOR_PASSWORD`).

Set `HELPDESK_SECRET` in any real deployment; it signs both the auth tokens and
the operator session cookie.

## API

"Auth" below means a bearer token for a user **of that tenant**.

| Method | Path | Tenant header | Auth | Notes |
|---|---|---|---|---|
| `GET` | `/health` | no | no | liveness; excluded from tenancy |
| `POST` | `/admin/tenants` | no | no | provision an organisation + its schema |
| `GET` | `/admin/tenants` | no | no | list all tenants |
| `POST` | `/admin/tenants/{identifier}/suspend` | no | no | suspend; its requests then 403 |
| `*` | `/panel/...` | no | operator | sqladmin UI over the tenant registry |
| `POST` | `/auth/register` | yes | no | create a user in that tenant |
| `POST` | `/auth/login` | yes | no | returns a bearer token |
| `POST` | `/auth/logout` | yes | agent | revokes the token server-side |
| `GET`/`PATCH` | `/users/me` | yes | agent | self-service profile |
| `GET` | `/me` | yes | agent | caller, tenant and resolved quota |
| `POST` | `/tickets` | yes | agent | create |
| `GET` | `/tickets` | yes | agent | list, `?ticket_status=` filter |
| `GET` | `/tickets/{id}` | yes | agent | read |
| `PATCH` | `/tickets/{id}` | yes | agent | partial update |
| `DELETE` | `/tickets/{id}` | yes | **admin** | delete |

### The security property worth reading

Users live in each tenant's own schema, so two customers can hold rows with the
same primary key. What stops Acme's token from working against Globex is that
the `AccessToken` table is *also* per-tenant: the row simply does not exist
when the request resolves elsewhere, so the lookup fails.
`test_auth.py::TestCrossTenantTokens` asserts exactly this. Move the token
table to a shared schema and the guarantee disappears silently.

The operator panel is a different trust domain: it is cross-tenant, sits
outside tenant resolution, and has its own session login. It deliberately
cannot create or delete tenants — a registry row without a schema is a tenant
whose every request fails, and deleting the row would orphan a live schema.

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

- **The JSON `/admin` routes have no authentication.** They provision and
  suspend tenants platform-wide. The `/panel` UI *is* authenticated; the JSON
  routes are left open to keep the provisioning example readable. Put them
  behind operator-only authn/authz, a separate port, or a private network
  before using this shape.
- **No migrations.** Tables are created at provisioning time from
  `Base.metadata`. A real deployment should use `TenantMigrationManager` so
  schema changes reach existing tenants — creating tables only works for
  tenants provisioned *after* the model changed.
- **The operator credential is a shared password.** `OperatorAuth` is where an
  SSO/OIDC integration belongs; the class exists to show the seam, not to
  recommend a shared secret.
- **OAuth is installed but not wired to a provider.** `fastapi-users[oauth]`
  is a dependency and `httpx-oauth` is available; adding a Google or GitHub
  client is a few lines in `auth.py`, but it needs real client credentials so
  it is left out of the runnable default.
- **No rate limiting.** It needs Redis, so it is off. `build_config()` shows
  the three settings to enable it, including `rate_limit_fail_closed`.
