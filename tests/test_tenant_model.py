"""Tenant model and TenantMetrics tests."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from fastapi_tenancy.core.types import (
    IsolationStrategy,
    Tenant,
    TenantMetrics,
    TenantStatus,
)


class TestTenantCreation:

    def test_minimal_tenant(self) -> None:
        t = Tenant(id="t1", identifier="acme-corp", name="Acme")
        assert t.id == "t1"
        assert t.identifier == "acme-corp"
        assert t.name == "Acme"
        assert t.status == TenantStatus.ACTIVE

    def test_default_status_active(self) -> None:
        t = Tenant(id="t1", identifier="acme", name="Acme")
        assert t.status == TenantStatus.ACTIVE

    def test_custom_status(self) -> None:
        t = Tenant(id="t1", identifier="acme", name="Acme", status=TenantStatus.SUSPENDED)
        assert t.status == TenantStatus.SUSPENDED

    def test_all_statuses(self) -> None:
        for status in TenantStatus:
            t = Tenant(id="t1", identifier="acme", name="Acme", status=status)
            assert t.status == status

    def test_metadata_default_empty(self) -> None:
        t = Tenant(id="t1", identifier="acme", name="Acme")
        assert t.metadata == {}

    def test_metadata_custom(self) -> None:
        t = Tenant(id="t1", identifier="acme", name="Acme", metadata={"plan": "pro"})
        assert t.metadata["plan"] == "pro"

    def test_isolation_strategy_set(self) -> None:
        t = Tenant(
            id="t1", identifier="acme", name="Acme",
            isolation_strategy=IsolationStrategy.SCHEMA,
        )
        assert t.isolation_strategy == IsolationStrategy.SCHEMA

    def test_database_url_optional(self) -> None:
        t = Tenant(id="t1", identifier="acme", name="Acme")
        assert t.database_url is None

    def test_schema_name_optional(self) -> None:
        t = Tenant(id="t1", identifier="acme", name="Acme")
        assert t.schema_name is None

    def test_created_at_defaults_to_now(self) -> None:
        before = datetime.now(UTC)
        t = Tenant(id="t1", identifier="acme", name="Acme")
        after = datetime.now(UTC)
        assert before <= t.created_at <= after

    def test_model_copy_immutability(self) -> None:
        t = Tenant(id="t1", identifier="acme", name="Acme")
        t2 = t.model_copy(update={"name": "Updated"})
        assert t.name == "Acme"
        assert t2.name == "Updated"


class TestTenantStatus:

    def test_is_active(self) -> None:
        t = Tenant(id="t1", identifier="a", name="A", status=TenantStatus.ACTIVE)
        assert t.is_active() is True

    def test_is_not_active_suspended(self) -> None:
        t = Tenant(id="t1", identifier="a", name="A", status=TenantStatus.SUSPENDED)
        assert t.is_active() is False

    def test_is_not_active_provisioning(self) -> None:
        t = Tenant(id="t1", identifier="a", name="A", status=TenantStatus.PROVISIONING)
        assert t.is_active() is False


class TestTenantFrozen:

    def test_tenant_is_frozen(self) -> None:
        t = Tenant(id="t1", identifier="acme", name="Acme")
        with pytest.raises((ValidationError, TypeError)):
            t.name = "changed"  # type: ignore[misc]


class TestTenantMetrics:

    def test_default_values(self) -> None:
        m = TenantMetrics(tenant_id="t1")
        assert m.tenant_id == "t1"
        assert m.requests_count >= 0
        assert m.storage_bytes >= 0
        assert m.users_count >= 0

    def test_custom_values(self) -> None:
        m = TenantMetrics(
            tenant_id="t1",
            requests_count=100,
            storage_bytes=1024,
            users_count=5,
            api_calls_today=50,
        )
        assert m.requests_count == 100
        assert m.storage_bytes == 1024

    def test_frozen(self) -> None:
        m = TenantMetrics(tenant_id="t1")
        with pytest.raises((ValidationError, TypeError)):
            m.requests_count = 999  # type: ignore[misc]

    def test_negative_count_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TenantMetrics(tenant_id="t1", requests_count=-1)

    def test_negative_storage_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TenantMetrics(tenant_id="t1", storage_bytes=-1)

    def test_negative_users_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TenantMetrics(tenant_id="t1", users_count=-1)


class TestIsolationStrategyEnum:

    def test_all_strategies(self) -> None:
        for strategy in IsolationStrategy:
            assert isinstance(strategy.value, str)

    def test_schema_value(self) -> None:
        assert IsolationStrategy.SCHEMA.value == "schema"

    def test_database_value(self) -> None:
        assert IsolationStrategy.DATABASE.value == "database"

    def test_rls_value(self) -> None:
        assert IsolationStrategy.RLS.value == "rls"

    def test_hybrid_value(self) -> None:
        assert IsolationStrategy.HYBRID.value == "hybrid"


class TestTenantStatusEnum:

    def test_all_statuses(self) -> None:
        assert TenantStatus.ACTIVE.value == "active"
        assert TenantStatus.SUSPENDED.value == "suspended"
        assert TenantStatus.PROVISIONING.value == "provisioning"
