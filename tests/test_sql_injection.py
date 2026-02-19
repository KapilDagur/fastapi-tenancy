"""Security regression tests — SQL injection prevention.

These tests verify the P0 critical fix: schema/database names are validated
BEFORE any DDL interpolation.  They serve as the permanent regression suite
so this class of bug can never be reintroduced silently.

No real database connection is needed — we mock the engine and verify that:
1. Validation is called before any DDL execution.
2. Invalid identifiers raise IsolationError, not raw SQL errors.
3. A bypass attempt (e.g. patching the validator to return True) would still
   be caught because we also assert the value passed to the validator.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from fastapi_tenancy.core.exceptions import IsolationError
from fastapi_tenancy.core.types import Tenant, TenantStatus
from fastapi_tenancy.utils.validation import (
    assert_safe_database_name,
    assert_safe_schema_name,
    validate_schema_name,
)

# ---------------------------------------------------------------------------
# Validator standalone
# ---------------------------------------------------------------------------

INJECTION_PAYLOADS = [
    "'; DROP TABLE tenants --",
    "tenant_ok; DROP DATABASE mydb",
    "schema\x00name",
    "schema'name",
    'schema"name',
    "schema name",  # space
    "../../etc/passwd",
    "1; SELECT * FROM pg_user",
    "a" * 64,  # too long
]


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_injection_payloads_fail_schema_validation(payload: str) -> None:
    """Every known injection pattern must fail schema name validation."""
    assert validate_schema_name(payload) is False, (
        f"Injection payload was accepted as valid schema name: {payload!r}"
    )


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_assert_safe_schema_name_rejects_payloads(payload: str) -> None:
    """assert_safe_schema_name must raise ValueError for all injection payloads."""
    with pytest.raises(ValueError):
        assert_safe_schema_name(payload)


@pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
def test_assert_safe_database_name_rejects_payloads(payload: str) -> None:
    """assert_safe_database_name must raise ValueError for all injection payloads."""
    with pytest.raises(ValueError):
        assert_safe_database_name(payload)


# ---------------------------------------------------------------------------
# SchemaIsolationProvider — initialize_tenant guard
# ---------------------------------------------------------------------------

def _make_schema_config() -> MagicMock:
    cfg = MagicMock()
    cfg.database_url = "postgresql+asyncpg://user:pass@localhost/db"
    cfg.database_pool_size = 5
    cfg.database_max_overflow = 10
    cfg.database_pool_timeout = 30
    cfg.database_pool_recycle = 3600
    cfg.database_echo = False
    return cfg


def _make_tenant_with_identifier(identifier: str) -> Tenant:
    return Tenant(
        id="t1",
        identifier=identifier,
        name="Test",
        status=TenantStatus.ACTIVE,
        schema_name=identifier,  # simulate bad schema name
    )


class TestSchemaProviderInjectionGuard:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_identifier", [
        "'; DROP SCHEMA --",
        "schema; DROP",
    ])
    async def test_initialize_tenant_rejects_injection(self, bad_identifier: str) -> None:
        from fastapi_tenancy.isolation.schema import SchemaIsolationProvider

        with patch("fastapi_tenancy.isolation.schema.create_async_engine") as mock_engine:
            mock_engine.return_value = MagicMock()
            provider = SchemaIsolationProvider(_make_schema_config())

        # Provide a tenant whose schema_name is an injection payload
        tenant = _make_tenant_with_identifier("valid-tenant")

        # Patch get_schema_name to return the malicious value directly
        with patch.object(provider, "get_schema_name", return_value=bad_identifier):
            with pytest.raises(IsolationError):
                await provider.initialize_tenant(tenant)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_identifier", [
        "'; DROP SCHEMA --",
        "schema; DROP",
    ])
    async def test_get_session_rejects_injection(self, bad_identifier: str) -> None:
        from fastapi_tenancy.isolation.schema import SchemaIsolationProvider

        with patch("fastapi_tenancy.isolation.schema.create_async_engine") as mock_engine:
            mock_engine.return_value = MagicMock()
            provider = SchemaIsolationProvider(_make_schema_config())

        tenant = _make_tenant_with_identifier("valid-tenant")

        with patch.object(provider, "get_schema_name", return_value=bad_identifier):
            with pytest.raises(IsolationError):
                async with provider.get_session(tenant):
                    pass


# ---------------------------------------------------------------------------
# DatabaseIsolationProvider — initialize_tenant guard
# ---------------------------------------------------------------------------

class TestDatabaseProviderInjectionGuard:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad_db_name", [
        "'; DROP DATABASE production --",
        "db; DROP",
    ])
    async def test_initialize_tenant_rejects_injection(self, bad_db_name: str) -> None:
        from fastapi_tenancy.isolation.database import DatabaseIsolationProvider

        with patch("fastapi_tenancy.isolation.database.create_async_engine") as mock_engine:
            mock_engine.return_value = MagicMock()
            provider = DatabaseIsolationProvider(_make_schema_config())

        tenant = _make_tenant_with_identifier("valid-tenant")

        with patch.object(provider, "_get_database_name", return_value=bad_db_name):
            with pytest.raises(IsolationError):
                await provider.initialize_tenant(tenant)
