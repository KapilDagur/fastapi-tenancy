"""Unit tests for fastapi_tenancy.core.types"""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from fastapi_tenancy.core.types import (
    AuditLog,
    IsolationProvider,
    IsolationStrategy,
    ResolutionStrategy,
    Tenant,
    TenantConfig,
    TenantMetrics,
    TenantResolver,
    TenantStatus,
)


class TestTenantStatus:
    def test_values_are_strings(self):
        assert TenantStatus.ACTIVE == "active"
        assert TenantStatus.SUSPENDED == "suspended"
        assert TenantStatus.DELETED == "deleted"
        assert TenantStatus.PROVISIONING == "provisioning"

    def test_all_members_present(self):
        assert set(TenantStatus) == {
            TenantStatus.ACTIVE,
            TenantStatus.SUSPENDED,
            TenantStatus.DELETED,
            TenantStatus.PROVISIONING,
        }

    def test_from_string(self):
        assert TenantStatus("active") is TenantStatus.ACTIVE


class TestIsolationStrategy:
    def test_values(self):
        assert IsolationStrategy.SCHEMA == "schema"
        assert IsolationStrategy.DATABASE == "database"
        assert IsolationStrategy.RLS == "rls"
        assert IsolationStrategy.HYBRID == "hybrid"


class TestResolutionStrategy:
    def test_values(self):
        assert ResolutionStrategy.HEADER == "header"
        assert ResolutionStrategy.SUBDOMAIN == "subdomain"
        assert ResolutionStrategy.PATH == "path"
        assert ResolutionStrategy.JWT == "jwt"
        assert ResolutionStrategy.CUSTOM == "custom"


class TestTenant:
    def test_create_minimal(self):
        t = Tenant(id="tid", identifier="acme-corp", name="Acme")
        assert t.id == "tid"
        assert t.identifier == "acme-corp"
        assert t.name == "Acme"
        assert t.status == TenantStatus.ACTIVE
        assert t.isolation_strategy is None
        assert t.metadata == {}
        assert t.database_url is None
        assert t.schema_name is None

    def test_frozen(self):
        t = Tenant(id="tid", identifier="acme-corp", name="Acme")
        with pytest.raises((ValidationError, TypeError)):
            t.name = "New Name"  # type: ignore[misc]

    def test_equality_by_id(self):
        t1 = Tenant(id="same", identifier="a-corp", name="A")
        t2 = Tenant(id="same", identifier="b-corp", name="B")
        assert t1 == t2

    def test_inequality(self):
        t1 = Tenant(id="id1", identifier="acme-corp", name="Acme")
        t2 = Tenant(id="id2", identifier="acme-corp", name="Acme")
        assert t1 != t2

    def test_not_equal_to_non_tenant(self):
        t = Tenant(id="tid", identifier="acme-corp", name="Acme")
        assert t != "not a tenant"
        assert t.__eq__("not a tenant") is NotImplemented

    def test_hashable(self):
        t1 = Tenant(id="tid", identifier="acme-corp", name="Acme")
        t2 = Tenant(id="tid", identifier="other-corp", name="Other")
        s = {t1, t2}
        assert len(s) == 1

    def test_repr(self):
        t = Tenant(id="tid", identifier="acme-corp", name="Acme")
        r = repr(t)
        assert "tid" in r
        assert "acme-corp" in r
        assert "active" in r

    def test_is_active(self):
        t = Tenant(id="tid", identifier="acme-corp", name="Acme", status=TenantStatus.ACTIVE)
        assert t.is_active() is True
        assert t.is_suspended() is False
        assert t.is_deleted() is False
        assert t.is_provisioning() is False

    def test_is_suspended(self):
        t = Tenant(id="tid", identifier="acme-corp", name="Acme", status=TenantStatus.SUSPENDED)
        assert t.is_active() is False
        assert t.is_suspended() is True

    def test_is_deleted(self):
        t = Tenant(id="tid", identifier="acme-corp", name="Acme", status=TenantStatus.DELETED)
        assert t.is_deleted() is True

    def test_is_provisioning(self):
        t = Tenant(id="tid", identifier="acme-corp", name="Acme", status=TenantStatus.PROVISIONING)
        assert t.is_provisioning() is True

    def test_model_dump_safe_masks_database_url(self):
        t = Tenant(
            id="tid",
            identifier="acme-corp",
            name="Acme",
            database_url="postgresql://user:secret@host/db",
        )
        data = t.model_dump_safe()
        assert data["database_url"] == "***masked***"

    def test_model_dump_safe_no_url(self):
        t = Tenant(id="tid", identifier="acme-corp", name="Acme")
        data = t.model_dump_safe()
        assert data["database_url"] is None

    def test_model_copy(self):
        t = Tenant(id="tid", identifier="acme-corp", name="Acme")
        t2 = t.model_copy(update={"name": "New Name"})
        assert t2.name == "New Name"
        assert t2.id == t.id


class TestTenantConfig:
    def test_defaults(self):
        cfg = TenantConfig()
        assert cfg.max_users is None
        assert cfg.max_storage_gb is None
        assert cfg.features_enabled == []
        assert cfg.rate_limit_per_minute == 100
        assert cfg.custom_settings == {}

    def test_custom_values(self):
        cfg = TenantConfig(
            max_users=50,
            max_storage_gb=10,
            features_enabled=["feature_a", "feature_b"],
            rate_limit_per_minute=200,
        )
        assert cfg.max_users == 50
        assert cfg.max_storage_gb == 10
        assert "feature_a" in cfg.features_enabled

    def test_frozen(self):
        cfg = TenantConfig()
        with pytest.raises((ValidationError, TypeError)):
            cfg.max_users = 100  # type: ignore[misc]

    def test_validate_from_metadata(self):
        meta = {"max_users": 500, "features_enabled": ["payments"]}
        cfg = TenantConfig.model_validate(meta)
        assert cfg.max_users == 500
        assert "payments" in cfg.features_enabled


class TestAuditLog:
    def test_create(self):
        log = AuditLog(tenant_id="tid", action="create", resource="order")
        assert log.tenant_id == "tid"
        assert log.action == "create"
        assert log.resource == "order"
        assert log.user_id is None
        assert log.resource_id is None

    def test_full_create(self):
        log = AuditLog(
            tenant_id="tid",
            user_id="user123",
            action="delete",
            resource="invoice",
            resource_id="inv-456",
            metadata={"reason": "test"},
            ip_address="127.0.0.1",
            user_agent="pytest",
        )
        assert log.user_id == "user123"
        assert log.resource_id == "inv-456"
        assert log.metadata["reason"] == "test"


class TestTenantMetrics:
    def test_defaults(self):
        m = TenantMetrics(tenant_id="tid")
        assert m.requests_count == 0
        assert m.storage_bytes == 0
        assert m.users_count == 0
        assert m.api_calls_today == 0
        assert m.last_activity is None


class TestProtocols:
    def test_tenant_resolver_protocol(self):
        class CookieResolver:
            async def resolve(self, request): ...

        r = CookieResolver()
        assert isinstance(r, TenantResolver)

    def test_isolation_provider_protocol(self):
        class FakeProvider:
            def get_session(self, tenant): ...

            async def apply_filters(self, query, tenant): ...

            async def initialize_tenant(self, tenant): ...

            async def destroy_tenant(self, tenant, **kw): ...

        p = FakeProvider()
        assert isinstance(p, IsolationProvider)

    def test_tenant_resolver_not_satisfied_without_resolve(self):
        class NotAResolver:
            def something_else(self): ...

        r = NotAResolver()
        assert not isinstance(r, TenantResolver)
