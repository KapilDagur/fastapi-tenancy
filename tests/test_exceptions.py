"""Complete exception hierarchy tests."""
from __future__ import annotations

import pytest

from fastapi_tenancy.core.exceptions import (
    ConfigurationError,
    DatabaseConnectionError,
    IsolationError,
    MigrationError,
    RateLimitExceededError,
    TenancyError,
    TenantDataLeakageError,
    TenantInactiveError,
    TenantNotFoundError,
    TenantQuotaExceededError,
    TenantResolutionError,
)


class TestTenancyError:

    def test_basic_message(self) -> None:
        exc = TenancyError("base error")
        assert str(exc) == "base error"
        assert exc.details == {}

    def test_with_details(self) -> None:
        exc = TenancyError("error", details={"key": "val"})
        assert "key" in str(exc)
        assert exc.details == {"key": "val"}

    def test_is_exception(self) -> None:
        with pytest.raises(TenancyError):
            raise TenancyError("test")


class TestTenantNotFoundError:

    def test_with_identifier(self) -> None:
        exc = TenantNotFoundError(identifier="acme-corp")
        assert "acme-corp" in str(exc)
        assert exc.identifier == "acme-corp"

    def test_without_identifier(self) -> None:
        exc = TenantNotFoundError()
        assert "not found" in str(exc).lower()
        assert exc.identifier is None

    def test_is_tenancy_error(self) -> None:
        assert issubclass(TenantNotFoundError, TenancyError)


class TestTenantResolutionError:

    def test_with_strategy(self) -> None:
        exc = TenantResolutionError(reason="header missing", strategy="header")
        assert "header" in str(exc)
        assert exc.reason == "header missing"
        assert exc.strategy == "header"

    def test_without_strategy(self) -> None:
        exc = TenantResolutionError(reason="parse failed")
        assert exc.strategy is None


class TestTenantInactiveError:

    def test_attributes(self) -> None:
        exc = TenantInactiveError(tenant_id="t1", status="suspended")
        assert exc.tenant_id == "t1"
        assert exc.status == "suspended"
        assert "suspended" in str(exc)


class TestIsolationError:

    def test_with_tenant(self) -> None:
        exc = IsolationError(operation="get_session", tenant_id="t1")
        assert exc.operation == "get_session"
        assert exc.tenant_id == "t1"
        assert "get_session" in str(exc)

    def test_without_tenant(self) -> None:
        exc = IsolationError(operation="init")
        assert exc.tenant_id is None


class TestConfigurationError:

    def test_attributes(self) -> None:
        exc = ConfigurationError(parameter="database_url", reason="required")
        assert exc.parameter == "database_url"
        assert exc.reason == "required"
        assert "database_url" in str(exc)


class TestMigrationError:

    def test_attributes(self) -> None:
        exc = MigrationError(tenant_id="t1", operation="upgrade", reason="locked")
        assert exc.tenant_id == "t1"
        assert exc.operation == "upgrade"
        assert exc.reason == "locked"
        assert "upgrade" in str(exc)


class TestRateLimitExceededError:

    def test_attributes(self) -> None:
        exc = RateLimitExceededError(tenant_id="t1", limit=100, window="1 minute")
        assert exc.limit == 100
        assert "100" in str(exc)


class TestTenantDataLeakageError:

    def test_security_prefix(self) -> None:
        exc = TenantDataLeakageError(
            operation="query",
            expected_tenant="t1",
            actual_tenant="t2",
        )
        assert "SECURITY" in str(exc)
        assert exc.expected_tenant == "t1"
        assert exc.actual_tenant == "t2"


class TestTenantQuotaExceededError:

    def test_attributes(self) -> None:
        exc = TenantQuotaExceededError(
            tenant_id="t1", quota_type="storage", current=100, limit=50
        )
        assert exc.quota_type == "storage"
        assert exc.current == 100
        assert exc.limit == 50


class TestDatabaseConnectionError:

    def test_attributes(self) -> None:
        exc = DatabaseConnectionError(tenant_id="t1", reason="timeout")
        assert exc.reason == "timeout"
        assert "t1" in str(exc)


class TestExceptionHierarchy:
    """All domain exceptions inherit from TenancyError."""

    @pytest.mark.parametrize("exc_class", [
        TenantNotFoundError,
        TenantResolutionError,
        TenantInactiveError,
        IsolationError,
        ConfigurationError,
        MigrationError,
        RateLimitExceededError,
        TenantDataLeakageError,
        TenantQuotaExceededError,
        DatabaseConnectionError,
    ])
    def test_is_tenancy_error(self, exc_class) -> None:
        assert issubclass(exc_class, TenancyError)

    def test_catch_all_with_base(self) -> None:
        raised_count = 0
        for exc in [
            TenantNotFoundError("x"),
            IsolationError("op"),
            MigrationError("t", "op", "reason"),
        ]:
            try:
                raise exc
            except TenancyError:
                raised_count += 1
        assert raised_count == 3
