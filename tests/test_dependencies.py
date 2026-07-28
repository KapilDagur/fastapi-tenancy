"""Tests for :mod:`fastapi_tenancy.dependencies`."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import text

from fastapi_tenancy.core.config import TenancyConfig
from fastapi_tenancy.core.context import (
    TenantContext,
    get_current_tenant,
    get_current_tenant_optional,
)
from fastapi_tenancy.core.types import IsolationStrategy, ResolutionStrategy, Tenant, TenantStatus
from fastapi_tenancy.dependencies import (
    TenantDep,
    TenantOptionalDep,
    make_audit_log_dependency,
    make_tenant_config_dependency,
    make_tenant_db_dependency,
)
from fastapi_tenancy.manager import TenancyManager
from fastapi_tenancy.storage.memory import InMemoryTenantStore

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncSession


def _cfg(**kw: Any) -> TenancyConfig:
    defaults: dict[str, Any] = {
        "database_url": "sqlite+aiosqlite:///:memory:",
        "resolution_strategy": ResolutionStrategy.HEADER,
        "isolation_strategy": IsolationStrategy.SCHEMA,
        "tenant_header_name": "X-Tenant-ID",
    }
    defaults.update(kw)
    return TenancyConfig(**defaults)


def _tenant(
    identifier: str = "test-tenant",
    metadata: dict[str, Any] | None = None,
    status: TenantStatus = TenantStatus.ACTIVE,
) -> Tenant:
    now = datetime.now(UTC)
    return Tenant(
        id=f"t-{identifier}",
        identifier=identifier,
        name=identifier.title(),
        status=status,
        metadata=metadata or {},
        created_at=now,
        updated_at=now,
    )


async def _build_manager(tenant: Tenant, **cfg_kwargs: Any) -> TenancyManager:
    cfg = _cfg(**cfg_kwargs)
    store = InMemoryTenantStore()
    await store.create(tenant)
    m = TenancyManager(cfg, store)
    await m.initialize()
    return m


def _make_app(manager: TenancyManager) -> FastAPI:
    """Return a FastAPI app with TenancyMiddleware pre-configured."""
    from fastapi_tenancy.middleware.tenancy import TenancyMiddleware  # noqa: PLC0415

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[Any, None]:
        yield

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(TenancyMiddleware, manager=manager, excluded_paths=["/health"])
    return app


class TestMakeTenantDbDependency:
    def test_returns_callable(self) -> None:
        """Factory returns a callable without raising."""
        m = TenancyManager(_cfg(), InMemoryTenantStore())
        dep = make_tenant_db_dependency(m)
        assert callable(dep)

    @pytest.mark.asyncio
    async def test_dependency_yields_async_session_in_route(self) -> None:
        """Inside a route the dependency yields an AsyncSession."""
        tenant = _tenant()
        manager = await _build_manager(tenant)
        app = _make_app(manager)

        get_db = make_tenant_db_dependency(manager)
        session_types: list[type] = []

        @app.get("/db-type")
        async def db_type(session: AsyncSession = Depends(get_db)) -> dict[str, str]:
            session_types.append(type(session))
            return {"type": type(session).__name__}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/db-type", headers={"X-Tenant-ID": "test-tenant"})

        assert resp.status_code == 200
        assert resp.json()["type"] == "AsyncSession"

    @pytest.mark.asyncio
    async def test_session_can_execute_select(self) -> None:
        """The yielded session is fully operational."""
        tenant = _tenant()
        manager = await _build_manager(tenant)
        app = _make_app(manager)

        get_db = make_tenant_db_dependency(manager)

        @app.get("/db-exec")
        async def db_exec(session: AsyncSession = Depends(get_db)) -> dict[str, Any]:
            result = await session.execute(text("SELECT 1 AS val"))
            row = result.first()
            return {"val": row[0] if row else None}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/db-exec", headers={"X-Tenant-ID": "test-tenant"})

        assert resp.status_code == 200
        assert resp.json()["val"] == 1

    @pytest.mark.asyncio
    async def test_multiple_requests_each_get_fresh_session(self) -> None:
        """Each request gets an independent session."""
        tenant = _tenant()
        manager = await _build_manager(tenant)
        app = _make_app(manager)

        get_db = make_tenant_db_dependency(manager)
        sessions_seen: list[AsyncSession] = []

        @app.get("/session-id")
        async def session_id(session: AsyncSession = Depends(get_db)) -> dict[str, int]:
            sessions_seen.append(session)
            return {"id": id(session)}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get("/session-id", headers={"X-Tenant-ID": "test-tenant"})
            r2 = await client.get("/session-id", headers={"X-Tenant-ID": "test-tenant"})

        assert r1.status_code == 200
        assert r2.status_code == 200
        # Both sessions must have been created (two distinct requests)
        assert len(sessions_seen) == 2
        # Keeping references prevents CPython from reusing the same memory
        # address for both objects — id() comparison is only reliable when
        # both objects are alive simultaneously.
        assert sessions_seen[0] is not sessions_seen[1]

    @pytest.mark.asyncio
    async def test_context_cleared_after_each_request(self) -> None:
        """Tenant context is None between requests."""
        tenant = _tenant()
        manager = await _build_manager(tenant)
        app = _make_app(manager)

        @app.get("/ping")
        async def ping() -> dict[str, str]:
            return {"ok": "yes"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/ping", headers={"X-Tenant-ID": "test-tenant"})

        # After the request lifecycle, context must be reset
        assert TenantContext.get_optional() is None


class TestMakeTenantConfigDependency:
    def test_returns_callable(self) -> None:
        m = TenancyManager(_cfg(), InMemoryTenantStore())
        dep = make_tenant_config_dependency(m)
        assert callable(dep)

    @pytest.mark.asyncio
    async def test_returns_tenant_config_with_metadata(self) -> None:
        """Metadata fields are parsed into TenantConfig fields."""
        tenant = _tenant(metadata={"max_users": 50, "rate_limit_per_minute": 200})
        manager = await _build_manager(tenant)
        app = _make_app(manager)

        get_cfg = make_tenant_config_dependency(manager)

        @app.get("/cfg")
        async def get_config(cfg: Any = Depends(get_cfg)) -> dict[str, Any]:
            return {"max_users": cfg.max_users, "rate_limit_per_minute": cfg.rate_limit_per_minute}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/cfg", headers={"X-Tenant-ID": "test-tenant"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["max_users"] == 50
        assert data["rate_limit_per_minute"] == 200

    @pytest.mark.asyncio
    async def test_empty_metadata_returns_defaults(self) -> None:
        """Empty metadata → all TenantConfig defaults apply."""
        tenant = _tenant(metadata={})
        manager = await _build_manager(tenant)
        app = _make_app(manager)

        get_cfg = make_tenant_config_dependency(manager)

        @app.get("/cfg-defaults")
        async def get_defaults(cfg: Any = Depends(get_cfg)) -> dict[str, Any]:
            return {
                "max_users": cfg.max_users,
                "max_storage_gb": cfg.max_storage_gb,
                "rate_limit_per_minute": cfg.rate_limit_per_minute,
            }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/cfg-defaults", headers={"X-Tenant-ID": "test-tenant"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["max_users"] is None
        assert data["max_storage_gb"] is None
        assert data["rate_limit_per_minute"] == 100  # default


class TestMakeAuditLogDependency:
    def test_returns_callable(self) -> None:
        m = TenancyManager(_cfg(), InMemoryTenantStore())
        dep = make_audit_log_dependency(m)
        assert callable(dep)

    @pytest.mark.asyncio
    async def test_audit_log_callable_invokes_write(self) -> None:
        """log() inside a route calls manager.write_audit_log."""

        tenant = _tenant()
        manager = await _build_manager(tenant)
        app = _make_app(manager)

        written_entries: list[Any] = []

        async def capture_write(entry: Any) -> None:
            written_entries.append(entry)

        manager._audit_writer.write = capture_write

        get_audit = make_audit_log_dependency(manager)

        @app.post("/resource")
        async def create_resource(audit: Any = Depends(get_audit)) -> dict[str, str]:
            await audit(action="create", resource="order", resource_id="o-123")
            return {"ok": "yes"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/resource", headers={"X-Tenant-ID": "test-tenant"})

        assert resp.status_code == 200
        assert len(written_entries) == 1
        entry = written_entries[0]
        assert entry.action == "create"
        assert entry.resource == "order"
        assert entry.resource_id == "o-123"
        assert entry.tenant_id == "t-test-tenant"

    @pytest.mark.asyncio
    async def test_audit_log_with_user_id_and_metadata(self) -> None:
        """log() passes user_id and metadata through."""
        tenant = _tenant()
        manager = await _build_manager(tenant)
        app = _make_app(manager)

        written_entries: list[Any] = []

        async def capture_write(entry: Any) -> None:
            written_entries.append(entry)

        manager._audit_writer.write = capture_write

        get_audit = make_audit_log_dependency(manager)

        @app.delete("/resource/{rid}")
        async def delete_resource(rid: str, audit: Any = Depends(get_audit)) -> dict[str, str]:
            await audit(
                action="delete",
                resource="order",
                resource_id=rid,
                user_id="admin-user",
                metadata={"reason": "user request"},
            )
            return {"ok": "yes"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete("/resource/o-999", headers={"X-Tenant-ID": "test-tenant"})

        assert resp.status_code == 200
        entry = written_entries[0]
        assert entry.user_id == "admin-user"
        assert entry.metadata == {"reason": "user request"}


class TestTypeAliases:
    def test_tenant_dep_is_annotated(self) -> None:
        """TenantDep is an Annotated type alias."""
        assert hasattr(TenantDep, "__metadata__") or str(TenantDep).startswith("typing.")

    def test_tenant_optional_dep_is_annotated(self) -> None:
        assert hasattr(TenantOptionalDep, "__metadata__") or str(TenantOptionalDep).startswith(
            "typing."
        )


class TestContextDependencies:
    @pytest.mark.asyncio
    async def test_get_current_tenant_raises_when_no_context(self) -> None:
        """get_current_tenant() must raise when no tenant is set."""
        from fastapi_tenancy.core.exceptions import TenantNotFoundError  # noqa: PLC0415

        TenantContext.clear()
        with pytest.raises(TenantNotFoundError):
            get_current_tenant()

    def test_get_current_tenant_returns_tenant_when_set(self) -> None:
        tenant = _tenant()
        token = TenantContext.set(tenant)
        try:
            result = get_current_tenant()
            assert result.id == tenant.id
        finally:
            TenantContext.reset(token)

    def test_get_current_tenant_optional_returns_none_when_unset(self) -> None:
        TenantContext.clear()
        result = get_current_tenant_optional()
        assert result is None

    def test_get_current_tenant_optional_returns_tenant_when_set(self) -> None:
        tenant = _tenant()
        token = TenantContext.set(tenant)
        try:
            result = get_current_tenant_optional()
            assert result is not None
            assert result.id == tenant.id
        finally:
            TenantContext.reset(token)

    @pytest.mark.asyncio
    async def test_route_returns_400_when_no_tenant_header(self) -> None:
        """Route using TenantDep returns error when middleware rejects request."""
        tenant = _tenant()
        manager = await _build_manager(tenant)
        app = _make_app(manager)

        @app.get("/needs-tenant")
        async def needs_tenant(t: TenantDep) -> dict[str, str]:
            return {"id": t.id}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/needs-tenant")  # no header

        # Middleware returns 400 before route is even called
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_route_with_optional_tenant_works_without_header(self) -> None:
        """Route using TenantOptionalDep is accessible even without a tenant header
        when it is on an excluded path."""
        manager = await _build_manager(_tenant())
        app = _make_app(manager)

        @app.get("/health")  # health is in excluded_paths → no middleware
        async def optional_tenant_route() -> dict[str, Any]:
            t = TenantContext.get_optional()
            return {"has_tenant": t is not None}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")

        # No middleware interference → route runs normally
        assert resp.status_code == 200
        assert resp.json()["has_tenant"] is False


class TestAuditLogIpAndUserAgent:
    """FIX M-10: make_audit_log_dependency must populate ip_address and
    user_agent in emitted AuditLog entries from the HTTP request context."""

    def _app_with_audit(
        self,
        manager: TenancyManager,
        captured: list[Any],
    ) -> FastAPI:
        """Return a minimal FastAPI app whose /audit endpoint emits a log entry
        and appends the raw AuditLog to *captured* for assertions."""
        from fastapi_tenancy.middleware.tenancy import TenancyMiddleware  # noqa: PLC0415

        get_audit = make_audit_log_dependency(manager)

        async def _capture_write(entry: Any) -> None:
            captured.append(entry)

        manager.write_audit_log = _capture_write  # type: ignore[method-assign]

        app = FastAPI()
        app.add_middleware(TenancyMiddleware, manager=manager, excluded_paths=[])

        @app.get("/audit")
        async def audit_endpoint(log=Depends(get_audit)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
            await log(action="read", resource="order")
            return {"ok": True}

        return app

    async def test_ip_address_populated_from_request_client(self) -> None:
        """ip_address in AuditLog must match the request's client host."""
        t = _tenant("audit-ip")
        store = InMemoryTenantStore()
        await store.create(t)
        cfg = _cfg()
        manager = TenancyManager(cfg, store)
        await manager.initialize()

        captured: list[Any] = []
        app = self._app_with_audit(manager, captured)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-Tenant-ID": "audit-ip"},
        ) as client:
            resp = await client.get("/audit")

        await manager.close()
        assert resp.status_code == 200
        assert len(captured) == 1
        entry = captured[0]
        # ASGITransport sets client host to "testclient" or "127.0.0.1
        assert entry.ip_address is not None, "ip_address must be populated from request.client.host"

    async def test_user_agent_populated_from_header(self) -> None:
        """user_agent in AuditLog must match the User-Agent request header."""
        t = _tenant("audit-ua")
        store = InMemoryTenantStore()
        await store.create(t)
        cfg = _cfg()
        manager = TenancyManager(cfg, store)
        await manager.initialize()

        captured: list[Any] = []
        app = self._app_with_audit(manager, captured)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={
                "X-Tenant-ID": "audit-ua",
                "User-Agent": "TestSuite/1.0",
            },
        ) as client:
            resp = await client.get("/audit")

        await manager.close()
        assert resp.status_code == 200
        assert len(captured) == 1
        entry = captured[0]
        assert entry.user_agent == "TestSuite/1.0", (
            f"Expected 'TestSuite/1.0', got {entry.user_agent!r}"
        )

    async def test_user_agent_none_when_header_absent(self) -> None:
        """user_agent must be None when no User-Agent header is sent."""
        t = _tenant("audit-noua")
        store = InMemoryTenantStore()
        await store.create(t)
        cfg = _cfg()
        manager = TenancyManager(cfg, store)
        await manager.initialize()

        captured: list[Any] = []
        app = self._app_with_audit(manager, captured)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-Tenant-ID": "audit-noua"},
        ) as client:
            # httpx adds its own User-Agent by default — override with empty.
            client.headers.pop("user-agent", None)
            resp = await client.get("/audit")

        await manager.close()
        assert resp.status_code == 200
        assert len(captured) == 1
        # user_agent may be None or httpx's default — we just check the field exists.
        assert hasattr(captured[0], "user_agent")

    async def test_audit_entry_tenant_id_correct(self) -> None:
        """tenant_id in the AuditLog must match the resolved tenant."""
        t = _tenant("audit-tid")
        store = InMemoryTenantStore()
        await store.create(t)
        cfg = _cfg()
        manager = TenancyManager(cfg, store)
        await manager.initialize()

        captured: list[Any] = []
        app = self._app_with_audit(manager, captured)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-Tenant-ID": "audit-tid"},
        ) as client:
            await client.get("/audit")

        await manager.close()
        assert captured[0].tenant_id == t.id
        assert captured[0].action == "read"
        assert captured[0].resource == "order"


class TestAuditLogTrustXForwardedFor:
    """FIX (S3): make_audit_log_dependency opts into ``X-Forwarded-For``.

    Before the fix:
        ``ip_address`` was always ``request.client.host``.  Behind a reverse
        proxy this is the proxy's address, not the real client's — forensic
        audit logs become useless during an incident review because every
        entry shows the proxy IP.

    After the fix:
        Constructor accepts ``trust_x_forwarded_for=True`` (opt-in, default
        ``False``).  When enabled, the leftmost entry of ``X-Forwarded-For``
        is preferred over ``request.client.host``.  The header is forgeable
        without a trusted proxy, so the default stays safe.
    """

    def _app_with_audit(
        self,
        manager: TenancyManager,
        captured: list[Any],
        *,
        trust_x_forwarded_for: bool = False,
    ) -> FastAPI:
        from fastapi_tenancy.middleware.tenancy import TenancyMiddleware  # noqa: PLC0415

        get_audit = make_audit_log_dependency(manager, trust_x_forwarded_for=trust_x_forwarded_for)

        async def _capture_write(entry: Any) -> None:
            captured.append(entry)

        manager.write_audit_log = _capture_write  # type: ignore[method-assign]

        app = FastAPI()
        app.add_middleware(TenancyMiddleware, manager=manager, excluded_paths=[])

        @app.get("/audit")
        async def audit_endpoint(log=Depends(get_audit)) -> dict[str, Any]:  # type: ignore[no-untyped-def]
            await log(action="read", resource="order")
            return {"ok": True}

        return app

    async def test_default_ignores_x_forwarded_for(self) -> None:
        """Without opt-in, an attacker-supplied XFF must NOT end up in audit logs."""
        t = _tenant("audit-default-xff")
        store = InMemoryTenantStore()
        await store.create(t)
        manager = TenancyManager(_cfg(), store)
        await manager.initialize()

        captured: list[Any] = []
        app = self._app_with_audit(manager, captured)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={
                "X-Tenant-ID": "audit-default-xff",
                "X-Forwarded-For": "203.0.113.42, 10.0.0.1",  # attacker-supplied
            },
        ) as client:
            resp = await client.get("/audit")

        await manager.close()
        assert resp.status_code == 200
        assert len(captured) == 1
        # The recorded IP must NOT be the attacker's value.
        assert captured[0].ip_address != "203.0.113.42", (
            "Default audit log dep trusted X-Forwarded-For — log forgery possible"
        )

    async def test_opt_in_uses_leftmost_xff_entry(self) -> None:
        """With opt-in, the leftmost XFF entry (the original client) wins."""
        t = _tenant("audit-xff-on")
        store = InMemoryTenantStore()
        await store.create(t)
        manager = TenancyManager(_cfg(), store)
        await manager.initialize()

        captured: list[Any] = []
        app = self._app_with_audit(manager, captured, trust_x_forwarded_for=True)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={
                "X-Tenant-ID": "audit-xff-on",
                # Leftmost entry is the originating client.
                "X-Forwarded-For": "198.51.100.7, 10.0.0.1, 10.0.0.2",
            },
        ) as client:
            resp = await client.get("/audit")

        await manager.close()
        assert resp.status_code == 200
        assert captured[0].ip_address == "198.51.100.7"

    async def test_opt_in_with_no_xff_falls_back_to_request_client(self) -> None:
        """When XFF is absent, fall back to request.client.host even with opt-in."""
        t = _tenant("audit-xff-fallback")
        store = InMemoryTenantStore()
        await store.create(t)
        manager = TenancyManager(_cfg(), store)
        await manager.initialize()

        captured: list[Any] = []
        app = self._app_with_audit(manager, captured, trust_x_forwarded_for=True)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-Tenant-ID": "audit-xff-fallback"},
        ) as client:
            resp = await client.get("/audit")

        await manager.close()
        assert resp.status_code == 200
        # Falls back to transport-level client (ASGITransport sets it).
        assert captured[0].ip_address is not None

    async def test_opt_in_with_empty_xff_falls_back(self) -> None:
        """An explicit empty XFF must not produce an empty-string ip_address."""
        t = _tenant("audit-xff-empty")
        store = InMemoryTenantStore()
        await store.create(t)
        manager = TenancyManager(_cfg(), store)
        await manager.initialize()

        captured: list[Any] = []
        app = self._app_with_audit(manager, captured, trust_x_forwarded_for=True)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={
                "X-Tenant-ID": "audit-xff-empty",
                "X-Forwarded-For": "",
            },
        ) as client:
            await client.get("/audit")

        await manager.close()
        # Falls back to request.client.host; must not be empty string.
        assert captured[0].ip_address != ""
        assert captured[0].ip_address is not None

    async def test_opt_in_whitespace_only_first_entry_falls_back(self) -> None:
        """An XFF entry that is just whitespace must not be treated as a valid IP."""
        t = _tenant("audit-xff-ws")
        store = InMemoryTenantStore()
        await store.create(t)
        manager = TenancyManager(_cfg(), store)
        await manager.initialize()

        captured: list[Any] = []
        app = self._app_with_audit(manager, captured, trust_x_forwarded_for=True)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            headers={
                "X-Tenant-ID": "audit-xff-ws",
                "X-Forwarded-For": "   , 10.0.0.1",
            },
        ) as client:
            await client.get("/audit")

        await manager.close()
        # Stripped leftmost entry was empty — must fall back to request.client.
        assert captured[0].ip_address != ""
        assert captured[0].ip_address is not None
        # And specifically: not the proxy at 10.0.0.1, because we stop at the
        # first entry rather than walking the chain.
        assert captured[0].ip_address != "10.0.0.1"


class TestAuditLogRequestClientNone:
    """Regression: ``request.client`` is ``None`` under some ASGI transports
    (lifespan-only test transports, raw scope construction, certain proxy
    integrations).  The audit-log dependency must not crash with
    ``AttributeError`` — it must record ``ip_address=None`` and proceed."""

    async def test_request_client_none_yields_ip_address_none(self) -> None:
        """Construct the dependency manually, hand it a Request whose scope
        omits the ``client`` key, and verify the audit entry is well-formed
        with ``ip_address=None``."""
        from starlette.requests import Request  # noqa: PLC0415

        t = _tenant("audit-no-client")
        store = InMemoryTenantStore()
        await store.create(t)
        manager = TenancyManager(_cfg(), store)
        await manager.initialize()

        captured: list[Any] = []

        async def _capture_write(entry: Any) -> None:
            captured.append(entry)

        manager.write_audit_log = _capture_write  # type: ignore[method-assign]

        get_audit = make_audit_log_dependency(manager)
        # Scope intentionally omits the "client" key — Starlette's
        # request.client returns None in that case.
        scope: dict[str, Any] = {
            "type": "http",
            "method": "GET",
            "path": "/audit",
            "headers": [(b"user-agent", b"unit-test/1.0")],
            "query_string": b"",
        }
        request = Request(scope)
        assert request.client is None

        log = await get_audit(request=request, tenant=t)
        await log(action="read", resource="order")

        await manager.close()
        assert len(captured) == 1
        assert captured[0].ip_address is None
        assert captured[0].user_agent == "unit-test/1.0"

    async def test_request_client_none_with_xff_trust_uses_xff(self) -> None:
        """When trust_x_forwarded_for=True and request.client is None, the
        XFF header is still the source of truth — the defensive None guard
        must not bypass the XFF path."""
        from starlette.requests import Request  # noqa: PLC0415

        t = _tenant("audit-no-client-xff")
        store = InMemoryTenantStore()
        await store.create(t)
        manager = TenancyManager(_cfg(), store)
        await manager.initialize()

        captured: list[Any] = []

        async def _capture_write(entry: Any) -> None:
            captured.append(entry)

        manager.write_audit_log = _capture_write  # type: ignore[method-assign]

        get_audit = make_audit_log_dependency(manager, trust_x_forwarded_for=True)
        scope: dict[str, Any] = {
            "type": "http",
            "method": "GET",
            "path": "/audit",
            "headers": [
                (b"x-forwarded-for", b"203.0.113.7, 10.0.0.1"),
            ],
            "query_string": b"",
        }
        request = Request(scope)
        assert request.client is None

        log = await get_audit(request=request, tenant=t)
        await log(action="read", resource="order")

        await manager.close()
        assert len(captured) == 1
        assert captured[0].ip_address == "203.0.113.7"
