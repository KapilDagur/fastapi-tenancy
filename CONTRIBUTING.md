# Contributing to fastapi-tenancy

Thank you for considering a contribution!  This guide covers the architecture,
development workflow, and expectations for pull requests.

---

## Architecture

### Module map

```
fastapi_tenancy/
│
├── __init__.py          ← stable public API surface (all re-exports here)
│
├── core/
│   ├── config.py        ← TenancyConfig (Pydantic Settings, env-var backed)
│   ├── context.py       ← TenantContext (ContextVar-based, request-scoped)
│   ├── exceptions.py    ← exception hierarchy rooted at TenancyError
│   └── types.py         ← Tenant (frozen Pydantic model), enums, ABCs
│
├── manager.py           ← TenancyManager: lifecycle orchestrator
│                           - no app in __init__
│                           - create_lifespan() registers middleware BEFORE yield
│
├── middleware/
│   └── tenancy.py       ← TenancyMiddleware (Starlette BaseHTTPMiddleware)
│                           - _is_path_skipped(path)     ← test-friendly
│                           - _should_skip_request(req)  ← full check
│
├── dependencies.py      ← FastAPI Depends helpers (get_tenant_db, etc.)
│
├── storage/
│   ├── tenant_store.py  ← TenantStore ABC — 8 abstract methods
│   ├── memory.py        ← InMemoryTenantStore (testing)
│   ├── postgres.py      ← SQLAlchemyTenantStore + deprecated PostgreSQLTenantStore alias
│   └── redis.py         ← RedisTenantStore (write-through cache)
│
├── isolation/
│   ├── base.py          ← BaseIsolationProvider ABC + Protocol
│   ├── schema.py        ← SchemaIsolationProvider
│   ├── database.py      ← DatabaseIsolationProvider
│   ├── rls.py           ← RLSIsolationProvider
│   ├── hybrid.py        ← HybridIsolationProvider (shared engine)
│   └── factory.py       ← IsolationProviderFactory
│
├── resolution/
│   ├── base.py          ← BaseTenantResolver ABC
│   ├── header.py        ← HeaderTenantResolver
│   ├── subdomain.py     ← SubdomainTenantResolver
│   ├── path.py          ← PathTenantResolver
│   ├── jwt.py           ← JWTTenantResolver
│   └── factory.py       ← ResolverFactory
│
├── cache/
│   └── tenant_cache.py  ← TenantCache (Redis, key-prefix isolated)
│
├── migrations/
│   └── manager.py       ← MigrationManager (Alembic, run_in_executor)
│
└── utils/
    ├── db_compat.py     ← DbDialect, detect_dialect, dialect helpers
    ├── security.py      ← assert_safe_schema_name, assert_safe_database_name
    └── validation.py    ← validate_tenant_identifier, sanitize_identifier
```

### Request lifecycle

```
HTTP Request
     │
     ▼
TenancyMiddleware.dispatch()
     │
     ├─ _should_skip_request()? → YES → call_next() → Response
     │
     ├─ resolver.resolve(request)
     │       ├─ HeaderTenantResolver → reads X-Tenant-ID header
     │       ├─ JWTTenantResolver    → decodes Bearer JWT
     │       ├─ SubdomainTenantResolver → parses hostname
     │       └─ PathTenantResolver   → reads URL segment
     │
     ├─ TenantStore.get_by_identifier(identifier)
     │
     ├─ tenant.is_active()? → NO → 403 response
     │
     ├─ TenantContext.set(tenant)         ← contextvars
     ├─ request.state.tenant = tenant
     │
     ├─ call_next(request) → Route Handler
     │       │
     │       └─ Depends(get_tenant_db)
     │               └─ IsolationProvider.get_session(tenant)
     │                       ├─ Schema: SET search_path TO tenant_xxx, public
     │                       ├─ Database: connect to tenant's database
     │                       ├─ RLS: SET app.current_tenant = tenant_id
     │                       └─ Hybrid: delegates to schema or rls
     │
     └─ finally: TenantContext.clear()   ← always, even on exception
```

### Key design decisions

| Decision | Rationale |
|----------|-----------|
| `Tenant` is frozen (immutable) | Prevents accidental shared-state mutation in async code |
| `ContextVar` default `None` for metadata | Avoids shared mutable `{}` default across tasks |
| Manager has no `app` in `__init__` | Decouples manager from FastAPI; enables testing without an app |
| Middleware registered before `yield` | Starlette freezes the middleware stack after startup |
| `%s`-style logging everywhere | Deferred string formatting; no CPU cost at log level below threshold |
| Security: no `available_headers` in errors | Prevents information disclosure of internal header names |
| `SQLAlchemyTenantStore` as preferred name | `PostgreSQLTenantStore` misleads; the class works with all dialects |

---

## Development setup

```bash
git clone https://github.com/KapilDagur/fastapi-tenancy
cd fastapi-tenancy
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Run tests:

```bash
pytest tests/ -m "unit or integration" -q
pytest tests/test_concurrent_isolation.py -v   # verify context isolation
```

Run linting:

```bash
ruff check src/ tests/
ruff format --check src/ tests/
mypy src/fastapi_tenancy/
```

Build docs:

```bash
pip install -e ".[docs]"
mkdocs serve    # live preview at http://127.0.0.1:8000
mkdocs build    # static site in site/
```

---

## Pull request guidelines

1. **Run the full test + lint suite** before opening a PR.
2. **Add tests** for any new behaviour — especially concurrent isolation tests
   for anything touching `TenantContext`.
3. **Update docstrings** — the API reference is auto-generated from them.
4. **No f-strings in `logger.*` calls** — use `%s` deferred formatting.
5. **No mutable defaults** in `ContextVar` or Pydantic fields.
6. **Security-sensitive changes** (DDL identifiers, error response contents,
   JWT parsing) require a comment explaining the threat model.

---

## Adding a new isolation strategy

1. Create `src/fastapi_tenancy/isolation/myname.py`.
2. Subclass `BaseIsolationProvider` and implement all four abstract methods.
3. Add `MY_STRATEGY = "my_strategy"` to `IsolationStrategy` in `core/types.py`.
4. Register in `IsolationProviderFactory.create()`.
5. Add dialect compatibility notes to `docs/guides/isolation.md`.
6. Add unit tests in `tests/isolation/test_myname.py`.

## Adding a new resolution strategy

1. Create `src/fastapi_tenancy/resolution/myname.py`.
2. Subclass `BaseTenantResolver` and implement `resolve(request) -> Tenant`.
3. Add `MY_RESOLVER = "my_resolver"` to `ResolutionStrategy` in `core/types.py`.
4. Register in `ResolverFactory.create()`.
5. Document in `docs/guides/resolution.md` and `docs/api/resolution.md`.
6. Add tests in `tests/resolution/test_myname.py`.

---

## Versioning

`fastapi-tenancy` follows [Semantic Versioning](https://semver.org/).  The
single source of truth for the version is `version = "x.y.z"` in
`pyproject.toml`.  `__init__.py` reads it via `importlib.metadata.version()`.
Never manually set `__version__` in `__init__.py`.
