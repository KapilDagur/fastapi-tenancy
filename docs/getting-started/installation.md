# Installation

## Requirements

| Dependency | Minimum version |
|---|---|
| Python | 3.11 |
| FastAPI | 0.111.0 |
| SQLAlchemy | 2.0.30 (async) |
| Pydantic | 2.6.0 |
| pydantic-settings | 2.2.0 |

## pip

```bash
# Choose your database driver (required)
pip install "fastapi-tenancy[postgres]"   # PostgreSQL via asyncpg
pip install "fastapi-tenancy[sqlite]"     # SQLite via aiosqlite (dev/CI)
pip install "fastapi-tenancy[mysql]"      # MySQL/MariaDB via aiomysql
pip install "fastapi-tenancy[mssql]"      # SQL Server via aioodbc
```

## uv (recommended)

```bash
uv add "fastapi-tenancy[postgres]"
```

## Optional extras

```bash
pip install "fastapi-tenancy[redis]"        # Redis write-through cache
pip install "fastapi-tenancy[jwt]"          # JWT tenant resolution (python-jose)
pip install "fastapi-tenancy[migrations]"   # Alembic tenant migrations
pip install "fastapi-tenancy[full]"         # postgres + redis + jwt + migrations
pip install "fastapi-tenancy[dev]"          # full + test + lint tooling
```

## Verify

```python
import fastapi_tenancy
print(fastapi_tenancy.__version__)  # e.g. "0.2.0"
```

## Type checking

`fastapi-tenancy` ships a `py.typed` marker ([PEP 561](https://peps.python.org/pep-0561/)).
No additional stubs are needed.

```bash
mypy your_app.py       # types from fastapi_tenancy flow through automatically
pyright your_app.py    # Pyright also supported
```
