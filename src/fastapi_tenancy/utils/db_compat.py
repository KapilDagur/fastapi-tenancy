"""Database compatibility utilities.

Detects the database dialect from a connection URL and provides
dialect-aware helpers used by isolation providers.

Supported databases
-------------------
- PostgreSQL (postgresql+asyncpg, postgresql+psycopg, postgresql) — full feature set
- SQLite (sqlite+aiosqlite, sqlite) — development / CI; no native schema support
- MySQL / MariaDB (mysql+aiomysql, mysql) — no native schema DDL via SET search_path
- Microsoft SQL Server (mssql+aioodbc, mssql) — partial; schemas via USE
- Any other dialect — falls back to "prefix" namespace mode

Schema isolation compatibility matrix
--------------------------------------
| Dialect    | Native SCHEMA | Fallback strategy                        |
|------------|--------------|------------------------------------------|
| PostgreSQL | ✓ yes        | CREATE SCHEMA + SET search_path          |
| SQLite     | ✗ no         | Table-name prefix (tenant_<slug>_<table>) |
| MySQL      | ✗ no*        | Per-tenant database (CREATE DATABASE)    |
| SQL Server | ✓ yes        | USE <db>; SET SCHEMA                     |
| Other      | ✗ no         | Table-name prefix (same as SQLite)       |

* MySQL SCHEMA == DATABASE; handled via DatabaseIsolationProvider.

RLS compatibility matrix
------------------------
| Dialect    | Native RLS | Alternative                              |
|------------|-----------|------------------------------------------|
| PostgreSQL | ✓ yes      | SET app.current_tenant + RLS policies    |
| All others | ✗ no       | Explicit WHERE tenant_id = :id filter    |
"""
from __future__ import annotations

import re
from enum import StrEnum


class DbDialect(StrEnum):
    """Known database dialect families."""
    POSTGRESQL = "postgresql"
    SQLITE = "sqlite"
    MYSQL = "mysql"
    MSSQL = "mssql"
    UNKNOWN = "unknown"


# Schemes that map to each dialect
_DIALECT_MAP: dict[str, DbDialect] = {
    "postgresql": DbDialect.POSTGRESQL,
    "postgresql+asyncpg": DbDialect.POSTGRESQL,
    "postgresql+psycopg": DbDialect.POSTGRESQL,
    "postgresql+psycopg2": DbDialect.POSTGRESQL,
    "asyncpg": DbDialect.POSTGRESQL,
    "sqlite": DbDialect.SQLITE,
    "sqlite+aiosqlite": DbDialect.SQLITE,
    "aiosqlite": DbDialect.SQLITE,
    "mysql": DbDialect.MYSQL,
    "mysql+aiomysql": DbDialect.MYSQL,
    "mysql+asyncmy": DbDialect.MYSQL,
    "mariadb": DbDialect.MYSQL,
    "mariadb+aiomysql": DbDialect.MYSQL,
    "mssql": DbDialect.MSSQL,
    "mssql+aioodbc": DbDialect.MSSQL,
}

_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+]*?)://", re.IGNORECASE)


def detect_dialect(database_url: str) -> DbDialect:
    """Return the :class:`DbDialect` for *database_url*.

    Examples
    --------
    >>> detect_dialect("postgresql+asyncpg://user:pass@host/db")
    <DbDialect.POSTGRESQL: 'postgresql'>
    >>> detect_dialect("sqlite+aiosqlite:///./test.db")
    <DbDialect.SQLITE: 'sqlite'>
    >>> detect_dialect("mysql+aiomysql://user:pass@host/db")
    <DbDialect.MYSQL: 'mysql'>
    """
    m = _SCHEME_RE.match(database_url.lower().strip())
    if not m:
        return DbDialect.UNKNOWN
    scheme = m.group(1)
    return _DIALECT_MAP.get(scheme, DbDialect.UNKNOWN)


def supports_native_schemas(dialect: DbDialect) -> bool:
    """Return True if the dialect natively supports CREATE SCHEMA + SET search_path.

    Only PostgreSQL (and MSSQL with some caveats) supports true schema-level
    isolation via DDL without creating separate databases.

    Examples
    --------
    >>> supports_native_schemas(DbDialect.POSTGRESQL)
    True
    >>> supports_native_schemas(DbDialect.SQLITE)
    False
    """
    return dialect in (DbDialect.POSTGRESQL, DbDialect.MSSQL)


def supports_native_rls(dialect: DbDialect) -> bool:
    """Return True if the dialect supports native Row-Level Security.

    Examples
    --------
    >>> supports_native_rls(DbDialect.POSTGRESQL)
    True
    >>> supports_native_rls(DbDialect.SQLITE)
    False
    """
    return dialect == DbDialect.POSTGRESQL


def get_set_tenant_sql(dialect: DbDialect, tenant_id: str) -> str | None:
    """Return the SQL statement that sets the current-tenant session variable.

    Returns ``None`` if the dialect has no equivalent mechanism (caller should
    use explicit WHERE filtering instead).

    Parameters
    ----------
    dialect:
        The target database dialect.
    tenant_id:
        Tenant ID value to embed.  Callers are responsible for using bind
        parameters; this function returns the statement *template* only.
    """
    if dialect == DbDialect.POSTGRESQL:
        return "SET app.current_tenant = :tenant_id"
    # MySQL / MariaDB user-defined variables.
    # NOTE: aiomysql and asyncmy do NOT support named bind parameters in
    # SET @var = :param statements.  The variable must be set with a literal
    # value.  The tenant_id here is an internal ID that has already been
    # validated and retrieved from the database — it is not raw user input.
    # We return None and handle MySQL via explicit-filter mode instead, which
    # is safer than building a literal SQL string.
    if dialect == DbDialect.MYSQL:
        return None  # MySQL: use apply_filters() WHERE tenant_id = :tid instead
    # MSSQL context_info / session_context (simplified)
    if dialect == DbDialect.MSSQL:
        return None  # Requires CONTEXT_INFO or SESSION_CONTEXT — not universally available
    return None  # SQLite and unknown — no session variable support


def _sanitize_identifier(identifier: str) -> str:
    """Inline copy of validation.sanitize_identifier.

    Kept here to avoid a circular import chain:
    db_compat → validation → (pydantic) which breaks when pydantic is not yet installed.
    Both copies must stay in sync.
    """
    import re as _re
    s = identifier.lower().replace("-", "_")
    s = _re.sub(r"[^a-z0-9_]", "_", s)
    s = _re.sub(r"_+", "_", s).strip("_")
    if s and not s[0].isalpha():
        s = f"t_{s}"
    return (s or "tenant")[:63]


def make_table_prefix(tenant_identifier: str) -> str:
    """Build a safe table-name prefix for dialects without native schema support.

    The prefix is derived from the tenant slug by replacing hyphens with
    underscores and truncating so that ``prefix + "_" + table_name`` fits
    within typical identifier limits (63 chars for most databases).

    Examples
    --------
    >>> make_table_prefix("acme-corp")
    't_acme_corp_'
    >>> make_table_prefix("my.company")
    't_my_company_'
    """
    safe = _sanitize_identifier(tenant_identifier)
    # _sanitize_identifier may prepend "t_" for digit-leading slugs.
    # Strip any leading "t_" before adding our own so we never get "t_t_…".
    base = safe.lstrip("t_") if safe.startswith("t_") else safe
    base = (base or safe)  # fallback if stripping consumed everything
    # Truncate so full "t_<base>_<tablename>" fits in 63-char limits
    prefix_base = base[:20].rstrip("_")
    return f"t_{prefix_base}_"


def get_schema_set_sql(dialect: DbDialect, schema_name: str) -> str | None:
    """Return the SQL to activate *schema_name* as the search path.

    Returns ``None`` if the dialect does not support this.
    """
    if dialect == DbDialect.POSTGRESQL:
        # Uses bind param — caller must set :schema
        return "SET search_path TO :schema, public"
    if dialect == DbDialect.MSSQL:
        return None  # MSSQL uses USE + ALTER USER — complex, not supported here
    return None


def requires_static_pool(dialect: DbDialect) -> bool:
    """Return True if the dialect requires SQLAlchemy's ``StaticPool``.

    SQLite's in-memory databases (``sqlite:///:memory:``) are per-connection
    and will appear empty on every new connection unless ``StaticPool`` is used
    to share a single underlying connection.
    """
    return dialect == DbDialect.SQLITE


__all__ = [
    "DbDialect",
    "detect_dialect",
    "get_schema_set_sql",
    "get_set_tenant_sql",
    "make_table_prefix",
    "requires_static_pool",
    "supports_native_rls",
    "supports_native_schemas",
]
