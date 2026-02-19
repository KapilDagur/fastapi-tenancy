# fastapi-tenancy — Complete Examples

Three self-contained, production-quality examples covering basic through advanced multi-tenancy patterns.

---

## Quick Start

```bash
# Clone and pick an example
cd 01-basic          # or 02-intermediate, 03-advanced

# Option A — Docker (recommended, zero setup)
docker compose up --build

# Option B — Local (SQLite, no Postgres)
pip install -r requirements.txt
uvicorn app.main:app --reload
```

---

## 01 — Basic: Notekeeper

**A simple note-taking SaaS. Start here.**

| Concern | Choice |
|---|---|
| Tenant resolution | HTTP header `X-Tenant-ID` |
| Data isolation | Row-Level Security (RLS) |
| Database | Single PostgreSQL DB, all tenants share tables |
| Tenant store | In-memory (dev) / Postgres (prod) |

### Architecture

```
Client ──── X-Tenant-ID: acme-corp ──► API
                                        │
                                   Middleware
                                   (resolve tenant)
                                        │
                                   RLS session
                          SET app.current_tenant = 'tenant-acme-001'
                                        │
                                   shared notes table
                          (RLS policy hides other tenants' rows)
```

### Try it

```bash
docker compose up --build

# Create a note for Acme
curl -X POST http://localhost:8000/notes \
  -H "X-Tenant-ID: acme-corp" \
  -H "Content-Type: application/json" \
  -d '{"title": "Hello", "body": "First note!"}'

# Globex cannot see Acme's notes
curl http://localhost:8000/notes -H "X-Tenant-ID: globex"
# → []
```

### Run tests (no Docker needed)

```bash
pip install -r requirements.txt
pytest tests/ -v --cov=app
```

---

## 02 — Intermediate: Invoicer

**A B2B invoicing SaaS. Schema isolation + Alembic + Redis.**

| Concern | Choice |
|---|---|
| Tenant resolution | Subdomain (acme.localhost:8000) |
| Data isolation | PostgreSQL schema per tenant |
| Migrations | Alembic, per-tenant `upgrade_all_tenants()` |
| Caching | Redis tenant cache |

### Architecture

```
acme.localhost:8000 ──► API
                         │
                    Middleware
                    (extract subdomain → resolve tenant)
                         │
                    Schema isolation
                    SET search_path TO tenant_acme_corp, public
                         │
                    tenant_acme_corp.customers
                    tenant_acme_corp.invoices
                    tenant_acme_corp.line_items
                    (completely separate from tenant_globex.*)
```

### Try it

```bash
# Add to /etc/hosts for subdomain routing:
# 127.0.0.1 acme.localhost globex.localhost

docker compose up --build

# Or use X-Tenant-ID header (fallback for dev):
curl http://localhost:8000/customers -H "X-Tenant-ID: acme-corp"

# Create customer and invoice
curl -X POST http://localhost:8000/customers \
  -H "X-Tenant-ID: acme-corp" \
  -H "Content-Type: application/json" \
  -d '{"name": "Bob Smith", "email": "bob@example.com"}'

curl http://localhost:8000/customers -H "X-Tenant-ID: globex"
# → []  (completely separate schema)
```

### Run tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

---

## 03 — Advanced: Projectr

**A full project-management SaaS. JWT · Hybrid isolation · Background workers · 3-tier tests.**

| Concern | Choice |
|---|---|
| Tenant resolution | JWT (`tenant_id` claim) |
| Data isolation | **Hybrid** — Enterprise → schema, Starter → RLS |
| Caching | Redis per-tenant cache with TTL + invalidation |
| Workers | Async background job processor |
| Tests | Unit (mocked) · Integration (SQLite) · E2E (Docker) |

### Tenant tiers

| Tier | Tenants | Isolation | Max projects |
|---|---|---|---|
| Enterprise | acme-corp, tech-corp | Dedicated schema | 500 |
| Starter | startup-x, dev-labs | Shared RLS | 3 |

### Architecture

```
Client ──── Authorization: Bearer <JWT> ──► API
                                             │
                                        Middleware
                                        (decode JWT → tenant_id claim)
                                             │
                                       HybridIsolationProvider
                                       ┌────┴────┐
                               Enterprise      Starter
                               (schema)        (RLS)
                           tenant_acme_corp   shared tables
                           .projects          WHERE tenant_id = ?
                           .tasks
```

### Try it

```bash
docker compose up --build

# Get a JWT for enterprise tenant
TOKEN=$(curl -s -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "acme-corp", "secret": "demo"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Create a project
curl -X POST http://localhost:8000/projects \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Mars Mission", "description": "Land on Mars by 2030"}'

# Get starter tenant token — hits project limit at 3
TOKEN2=$(curl -s -X POST http://localhost:8000/auth/token \
  -d '{"tenant_id": "startup-x", "secret": "demo"}' \
  -H "Content-Type: application/json" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# Cross-tenant isolation — startup cannot see acme's projects
curl -H "Authorization: Bearer $TOKEN2" http://localhost:8000/projects
# → []
```

### Run tests

```bash
pip install -r requirements.txt

# Unit tests only (fastest, zero I/O)
pytest tests/unit/ -v

# Integration tests (SQLite, no Docker)
pytest tests/integration/ -v

# E2E (requires docker compose up --build)
pytest tests/e2e/ -v -m e2e

# All non-E2E
pytest tests/ -v --cov=app
```

---

## Key concepts illustrated

| Example | Resolution | Isolation | Store | Cache | Migrations | Auth |
|---|---|---|---|---|---|---|
| 01-basic | Header | RLS | In-memory | — | — | None |
| 02-intermediate | Subdomain | Schema | PostgreSQL | Redis | Alembic | None |
| 03-advanced | JWT | Hybrid | In-memory | Redis | — | JWT |

## Adding your own tenant

All three examples use `InMemoryTenantStore` by default. In production, swap to
`PostgreSQLTenantStore` and add a tenant via:

```python
from fastapi_tenancy import Tenant
from fastapi_tenancy.storage.postgres import PostgreSQLTenantStore

store = PostgreSQLTenantStore(database_url="postgresql+asyncpg://...")
await store.initialize()

tenant = Tenant(id="my-id", identifier="my-company", name="My Company")
await store.create(tenant)
await isolation_provider.initialize_tenant(tenant)  # CREATE SCHEMA / RLS setup
```
