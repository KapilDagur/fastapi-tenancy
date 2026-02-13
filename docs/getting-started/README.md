# Getting Started with FastAPI Tenancy

This guide will help you add multi-tenancy to your FastAPI application in minutes.

## Prerequisites

- Python 3.11 or higher
- FastAPI 0.109.0 or higher
- PostgreSQL 12 or higher
- (Optional) Redis 6 or higher for caching

## Installation

```bash
pip install fastapi-tenancy
```

Or with UV:

```bash
uv add fastapi-tenancy
```

## Quick Start

### 1. Basic Setup

Create a simple multi-tenant FastAPI application:

```python
from fastapi import FastAPI, Depends
from fastapi_tenancy import TenancyManager, TenancyConfig
from fastapi_tenancy.core.context import get_current_tenant
from fastapi_tenancy.core.types import Tenant

# Create FastAPI app
app = FastAPI(title="My Multi-Tenant API")

# Configure tenancy
config = TenancyConfig(
    database_url="postgresql+asyncpg://user:password@localhost:5432/mydb",
    resolution_strategy="header",  # Resolve tenant from X-Tenant-ID header
    isolation_strategy="schema",   # Each tenant gets own schema
)

# Initialize tenancy
tenancy = TenancyManager(app, config)

# Create an endpoint
@app.get("/api/users")
async def get_users(tenant: Tenant = Depends(get_current_tenant)):
    """Get users for the current tenant."""
    return {
        "tenant": tenant.identifier,
        "users": [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ]
    }
```

### 2. Run the Application

```bash
uvicorn main:app --reload
```

### 3. Make Requests

```bash
# Request for tenant "acme"
curl -H "X-Tenant-ID: acme" http://localhost:8000/api/users

# Request for tenant "globex"
curl -H "X-Tenant-ID: globex" http://localhost:8000/api/users
```

## Understanding the Basics

### Tenant Resolution

FastAPI Tenancy needs to identify which tenant each request belongs to. This is called "tenant resolution". There are several strategies:

#### Header-Based (Recommended for APIs)

```python
config = TenancyConfig(
    resolution_strategy="header",
    tenant_header_name="X-Tenant-ID",
    ...
)
```

Request:
```bash
curl -H "X-Tenant-ID: acme" http://localhost:8000/api/users
```

#### Subdomain-Based (Recommended for Web Apps)

```python
config = TenancyConfig(
    resolution_strategy="subdomain",
    domain_suffix=".yourapp.com",
    ...
)
```

Request:
```bash
curl http://acme.yourapp.com/api/users
```

#### Path-Based

```python
config = TenancyConfig(
    resolution_strategy="path",
    path_prefix="/tenants",
    ...
)
```

Request:
```bash
curl http://localhost:8000/tenants/acme/api/users
```

#### JWT-Based

```python
config = TenancyConfig(
    resolution_strategy="jwt",
    jwt_secret="your-secret-key",
    jwt_tenant_claim="tenant_id",
    ...
)
```

Request:
```bash
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/users
```

### Data Isolation

FastAPI Tenancy ensures each tenant's data is isolated. There are several strategies:

#### Schema Per Tenant (Recommended)

Each tenant gets their own PostgreSQL schema:

```
Database: mydb
├── Schema: public (shared tables: tenants, users, etc.)
├── Schema: tenant_acme (Acme's data)
├── Schema: tenant_globex (Globex's data)
└── Schema: tenant_initech (Initech's data)
```

**Pros:**
- Good isolation
- Easy backups per tenant
- Moderate scalability (100-1000s tenants)

**Cons:**
- Connection pool overhead
- Schema limit per database

#### Row-Level Security (High Scale)

All tenants share the same schema, with PostgreSQL RLS policies:

```
Database: mydb
└── Schema: public
    ├── Table: users (with tenant_id column)
    ├── Table: orders (with tenant_id column)
    └── RLS Policies enforce tenant_id filtering
```

**Pros:**
- Highest scalability (millions of tenants)
- Efficient resource usage
- Simple backups

**Cons:**
- Requires careful query construction
- RLS policy overhead

#### Database Per Tenant (Enterprise)

Each tenant gets a complete database:

```
Server
├── Database: tenant_acme
├── Database: tenant_globex
└── Database: tenant_initech
```

**Pros:**
- Maximum isolation
- Complete customization per tenant
- Easy compliance (GDPR, etc.)

**Cons:**
- Higher resource usage
- Complex management

#### Hybrid (Flexible)

Mix strategies based on tenant tier:

```python
config = TenancyConfig(
    isolation_strategy="hybrid",
    premium_tenants=["acme", "globex"],  # Get dedicated schemas
    premium_isolation_strategy="schema",
    standard_isolation_strategy="rls",   # Share with RLS
    ...
)
```

## Database Setup

### 1. Create Database

```sql
CREATE DATABASE mydb;
```

### 2. Initialize Tenancy Tables

FastAPI Tenancy needs a few tables to manage tenants:

```python
from fastapi_tenancy.migrations import initialize_tenancy_tables

# In your startup code
await initialize_tenancy_tables(config.database_url)
```

Or run the migration script:

```bash
tenancy-migrate init --database-url postgresql://user:pass@localhost/mydb
```

### 3. Create a Tenant

```python
from fastapi_tenancy.storage import TenantStore

store = TenantStore(config)

# Create tenant
tenant = await store.create_tenant(
    identifier="acme",
    name="Acme Corporation",
)

# Initialize tenant's schema/database
await store.provision_tenant(tenant)
```

Or use the CLI:

```bash
tenancy-admin create-tenant \
    --identifier acme \
    --name "Acme Corporation" \
    --database-url postgresql://user:pass@localhost/mydb
```

## Working with Databases

### Using SQLAlchemy

```python
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi_tenancy.dependencies import get_tenant_db

@app.get("/api/users")
async def get_users(db: AsyncSession = Depends(get_tenant_db)):
    """Database session is automatically scoped to current tenant."""
    result = await db.execute(select(User))
    users = result.scalars().all()
    return users
```

### Defining Models

```python
from sqlalchemy import Column, Integer, String
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
```

For RLS strategy, add tenant_id:

```python
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
```

## Next Steps

- [Configuration Reference](configuration.md) - All configuration options
- [Resolution Strategies](../strategies/resolution.md) - Detailed guide on tenant resolution
- [Isolation Strategies](../strategies/isolation.md) - Detailed guide on data isolation
- [Security Guide](../guides/security.md) - Security best practices
- [Deployment Guide](../guides/deployment.md) - Production deployment
- [API Reference](../api/reference.md) - Complete API documentation

## Common Patterns

### Multi-Tenant Endpoints

```python
@app.get("/api/stats")
async def get_stats(
    tenant: Tenant = Depends(get_current_tenant),
    db: AsyncSession = Depends(get_tenant_db),
):
    """Get statistics for current tenant."""
    user_count = await db.scalar(select(func.count(User.id)))
    order_count = await db.scalar(select(func.count(Order.id)))

    return {
        "tenant": tenant.identifier,
        "users": user_count,
        "orders": order_count,
    }
```

### Optional Tenant Endpoints

Some endpoints might work with or without a tenant:

```python
from fastapi_tenancy.core.context import get_current_tenant_optional

@app.get("/api/public-info")
async def public_info(tenant: Tenant | None = Depends(get_current_tenant_optional)):
    """Endpoint that works with or without tenant."""
    if tenant:
        return {"message": f"Hello, {tenant.name}!"}
    return {"message": "Hello, anonymous user!"}
```

### Tenant-Specific Configuration

```python
@app.get("/api/features")
async def get_features(tenant: Tenant = Depends(get_current_tenant)):
    """Get features available to tenant."""
    return {
        "tenant": tenant.identifier,
        "features": tenant.metadata.get("features", []),
        "max_users": tenant.metadata.get("max_users", 10),
    }
```

## Troubleshooting

### "Tenant not found" Error

Make sure tenant exists in database and your request includes tenant identifier:

```bash
# Check tenant exists
tenancy-admin list-tenants --database-url postgresql://...

# Include tenant in request
curl -H "X-Tenant-ID: acme" http://localhost:8000/api/users
```

### Connection Pool Exhausted

Adjust pool settings in configuration:

```python
config = TenancyConfig(
    database_pool_size=50,  # Increase from default 20
    database_max_overflow=100,  # Increase from default 40
    ...
)
```

### Slow Queries

Enable query logging to identify issues:

```python
config = TenancyConfig(
    database_echo=True,  # Log all SQL queries
    enable_query_logging=True,
    slow_query_threshold_ms=100,
    ...
)
```

## Need Help?

- 📖 [Full Documentation](https://fastapi-tenancy.readthedocs.io)
- 💬 [GitHub Discussions](https://github.com/KapilDagur/fastapi-tenancy/discussions)
- 🐛 [Report Issues](https://github.com/KapilDagur/fastapi-tenancy/issues)
- 📧 Email: support@fastapi-tenancy.dev
