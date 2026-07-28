"""The sqladmin operator panel over the tenant registry.

The panel is the cross-tenant control plane, so the tests that matter are the
ones about *reachability*: it must be unauthenticated-proof, and it must sit
outside tenant resolution rather than accidentally requiring a tenant header.
"""

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from _helpers import Provision
    import httpx

pytestmark = pytest.mark.e2e

_OPERATOR = {"username": "operator", "password": "operator-dev-password"}


class TestPanelAccess:
    async def test_panel_requires_login(self, client: "httpx.AsyncClient") -> None:
        """An anonymous visitor is redirected to the login form, not served."""
        resp = await client.get("/panel/", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert "/panel/login" in resp.headers["location"]

    async def test_panel_is_outside_tenant_resolution(self, client: "httpx.AsyncClient") -> None:
        """No ``X-Tenant-ID`` is sent, and the panel must not demand one.

        If ``/panel`` were missing from ``excluded_paths`` the tenancy
        middleware would reject this with 400 before sqladmin ever ran.
        """
        resp = await client.get("/panel/login")
        assert resp.status_code == 200

    async def test_wrong_credentials_are_refused(self, client: "httpx.AsyncClient") -> None:
        resp = await client.post(
            "/panel/login",
            data={"username": "operator", "password": "wrong"},
            follow_redirects=False,
        )
        # sqladmin re-renders the form rather than redirecting into the panel.
        assert resp.status_code != 302 or "/panel/login" in resp.headers.get("location", "")

    async def test_operator_can_log_in_and_list_tenants(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        await provision("panel-corp")

        login = await client.post("/panel/login", data=_OPERATOR, follow_redirects=False)
        assert login.status_code in (302, 307)

        # "tenant-model", not "tenant": sqladmin derives the segment from the
        # mapped class name, and overrides of `identity` are ignored.
        listing = await client.get("/panel/tenant-model/list")
        assert listing.status_code == 200
        assert "panel-corp" in listing.text

    async def test_logout_ends_the_session(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        await provision("panel-corp")
        await client.post("/panel/login", data=_OPERATOR, follow_redirects=False)
        assert (await client.get("/panel/tenant-model/list")).status_code == 200

        await client.get("/panel/logout", follow_redirects=False)

        after = await client.get("/panel/tenant-model/list", follow_redirects=False)
        assert after.status_code in (302, 307)


class TestPanelSafety:
    async def test_credentials_are_never_rendered(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        """``database_url`` can carry a password for DATABASE-isolated tenants.

        It is excluded from every view, not merely from the list, so a details
        page cannot leak it either.
        """
        await provision("panel-corp")
        await client.post("/panel/login", data=_OPERATOR, follow_redirects=False)

        listing = await client.get("/panel/tenant-model/list")
        assert "database_url" not in listing.text.lower()

    async def test_panel_cannot_create_tenants(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        """Creating a registry row here would leave a tenant with no schema.

        Provisioning has to go through ``manager.register_tenant()``, which
        creates the schema and its tables; a row without one is a tenant whose
        every request fails.
        """
        await provision("panel-corp")
        await client.post("/panel/login", data=_OPERATOR, follow_redirects=False)

        resp = await client.get("/panel/tenant-model/create", follow_redirects=False)
        assert resp.status_code in (403, 404, 302, 307)

    async def test_panel_cannot_delete_tenants(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        """Deleting the row would orphan a live schema holding customer data."""
        created = await provision("panel-corp")
        await client.post("/panel/login", data=_OPERATOR, follow_redirects=False)

        resp = await client.post(
            "/panel/tenant-model/delete",
            data={"pks": created["id"]},
            follow_redirects=False,
        )
        assert resp.status_code in (403, 404, 405, 302, 307)

        # The tenant is still there and still serving.
        listing = await client.get("/panel/tenant-model/list")
        assert "panel-corp" in listing.text
