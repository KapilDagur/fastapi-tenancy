# Guides

In-depth guides for every major feature of fastapi-tenancy.

| Guide | When to read |
|---|---|
| [Isolation Strategies](isolation.md) | Choosing between schema, database, RLS, hybrid |
| [Resolution Strategies](resolution.md) | Header, subdomain, JWT, path, custom resolvers |
| [Middleware & Lifespan](middleware.md) | FastAPI lifespan, middleware timing, manual wiring |
| [FastAPI Dependencies](dependencies.md) | `get_tenant_db`, `get_current_tenant`, custom deps |
| [Testing](testing.md) | Unit tests, concurrent isolation tests, fixtures |
| [Migrations](migrations.md) | Alembic per-tenant migrations |
| [Production Deployment](production.md) | Pool sizing, PostgreSQL tuning, observability |
