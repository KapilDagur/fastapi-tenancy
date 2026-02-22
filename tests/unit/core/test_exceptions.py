"""Unit tests for fastapi_tenancy.core.exceptions"""

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
    def test_basic(self):
        exc = TenancyError("something went wrong")
        assert exc.message == "something went wrong"
        assert exc.details == {}
        assert str(exc) == "something went wrong"

    def test_with_details(self):
        exc = TenancyError("error", details={"key": "value"})
        assert exc.details == {"key": "value"}
        assert "details=" in str(exc)

    def test_repr(self):
        exc = TenancyError("msg")
        r = repr(exc)
        assert "TenancyError" in r
        assert "msg" in r

    def test_is_exception(self):
        with pytest.raises(TenancyError):
            raise TenancyError("test")


class TestTenantNotFoundError:
    def test_with_identifier(self):
        exc = TenantNotFoundError(identifier="acme-corp")
        assert "acme-corp" in exc.message
        assert exc.identifier == "acme-corp"

    def test_without_identifier(self):
        exc = TenantNotFoundError()
        assert "Tenant not found" in exc.message
        assert exc.identifier is None

    def test_with_details(self):
        exc = TenantNotFoundError(identifier="x", details={"hint": "check store"})
        assert exc.details["hint"] == "check store"

    def test_is_tenancy_error(self):
        assert isinstance(TenantNotFoundError(), TenancyError)


class TestTenantResolutionError:
    def test_with_strategy(self):
        exc = TenantResolutionError(reason="header missing", strategy="header")
        assert "header missing" in exc.message
        assert "header" in exc.message
        assert exc.reason == "header missing"
        assert exc.strategy == "header"

    def test_without_strategy(self):
        exc = TenantResolutionError(reason="bad request")
        assert "bad request" in exc.message
        assert exc.strategy is None

    def test_with_details(self):
        exc = TenantResolutionError(reason="x", details={"claim": "tenant_id"})
        assert exc.details["claim"] == "tenant_id"


class TestTenantInactiveError:
    def test_basic(self):
        exc = TenantInactiveError(tenant_id="t1", status="suspended")
        assert "t1" in exc.message
        assert "suspended" in exc.message
        assert exc.tenant_id == "t1"
        assert exc.status == "suspended"


class TestIsolationError:
    def test_with_tenant(self):
        exc = IsolationError(operation="get_session", tenant_id="t1")
        assert "get_session" in exc.message
        assert "t1" in exc.message
        assert exc.operation == "get_session"
        assert exc.tenant_id == "t1"

    def test_without_tenant(self):
        exc = IsolationError(operation="init")
        assert exc.tenant_id is None

    def test_with_details(self):
        exc = IsolationError(operation="x", details={"schema": "tenant_acme"})
        assert exc.details["schema"] == "tenant_acme"


class TestConfigurationError:
    def test_basic(self):
        exc = ConfigurationError(parameter="jwt_secret", reason="must be >= 32 chars")
        assert "jwt_secret" in exc.message
        assert "32 chars" in exc.message
        assert exc.parameter == "jwt_secret"
        assert exc.reason == "must be >= 32 chars"


class TestMigrationError:
    def test_basic(self):
        exc = MigrationError(tenant_id="t1", operation="upgrade", reason="no alembic.ini")
        assert "t1" in exc.message
        assert "upgrade" in exc.message
        assert "alembic.ini" in exc.message
        assert exc.tenant_id == "t1"
        assert exc.operation == "upgrade"
        assert exc.reason == "no alembic.ini"


class TestRateLimitExceededError:
    def test_basic(self):
        exc = RateLimitExceededError(tenant_id="t1", limit=100, window_seconds=60)
        assert "t1" in exc.message
        assert exc.limit == 100
        assert exc.window_seconds == 60

    def test_with_details(self):
        exc = RateLimitExceededError(
            tenant_id="t1", limit=10, window_seconds=60, details={"retry_after": 5}
        )
        assert exc.details["retry_after"] == 5


class TestTenantDataLeakageError:
    def test_basic(self):
        exc = TenantDataLeakageError(
            operation="query",
            expected_tenant="t1",
            actual_tenant="t2",
        )
        assert "SECURITY" in exc.message
        assert "t1" in exc.message
        assert "t2" in exc.message
        assert exc.operation == "query"
        assert exc.expected_tenant == "t1"
        assert exc.actual_tenant == "t2"


class TestTenantQuotaExceededError:
    def test_basic(self):
        exc = TenantQuotaExceededError(
            tenant_id="t1",
            quota_type="users",
            current=110,
            limit=100,
        )
        assert "t1" in exc.message
        assert "users" in exc.message
        assert exc.current == 110
        assert exc.limit == 100


class TestDatabaseConnectionError:
    def test_basic(self):
        exc = DatabaseConnectionError(tenant_id="t1", reason="timeout")
        assert "t1" in exc.message
        assert "timeout" in exc.message
        assert exc.tenant_id == "t1"
        assert exc.reason == "timeout"


class TestHierarchy:
    def test_all_subclass_tenancy_error(self):
        for cls in [
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
        ]:
            assert issubclass(cls, TenancyError), f"{cls.__name__} must subclass TenancyError"

    def test_catchable_as_base(self):
        try:
            raise TenantNotFoundError("acme")
        except TenancyError as exc:
            assert "acme" in exc.message  # noqa: PT017
