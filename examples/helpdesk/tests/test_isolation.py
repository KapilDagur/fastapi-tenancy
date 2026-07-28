"""The guarantee the example exists to demonstrate: tenants cannot see each other.

Every CRUD verb is checked across the boundary, not just reads — a write path
that resolved the wrong schema would be worse than a leaky read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from _helpers import OpenTicket, Provision
    import httpx

pytestmark = pytest.mark.e2e

_ALPHA = {"X-Tenant-ID": "iso-alpha"}
_BETA = {"X-Tenant-ID": "iso-beta"}


@pytest.fixture(autouse=True)
async def _two_tenants(provision: Provision) -> None:
    await provision("iso-alpha")
    await provision("iso-beta")


class TestReadIsolation:
    async def test_list_shows_only_own_tickets(
        self,
        client: httpx.AsyncClient,
        open_ticket: OpenTicket,
    ) -> None:
        await open_ticket("iso-alpha", "alpha printer on fire")
        await open_ticket("iso-alpha", "alpha vpn down")
        await open_ticket("iso-beta", "beta billing question")

        alpha = (await client.get("/tickets", headers=_ALPHA)).json()
        beta = (await client.get("/tickets", headers=_BETA)).json()

        assert {t["subject"] for t in alpha} == {"alpha printer on fire", "alpha vpn down"}
        assert {t["subject"] for t in beta} == {"beta billing question"}

    async def test_direct_object_reference_is_refused(
        self,
        client: httpx.AsyncClient,
        open_ticket: OpenTicket,
    ) -> None:
        """Guessing another tenant's id must 404, not return the row."""
        secret = await open_ticket("iso-beta", "beta secret")

        assert (await client.get(f"/tickets/{secret['id']}", headers=_ALPHA)).status_code == 404

        own = await client.get(f"/tickets/{secret['id']}", headers=_BETA)
        assert own.status_code == 200
        assert own.json()["subject"] == "beta secret"

    async def test_each_tenant_has_its_own_id_sequence(
        self,
        client: httpx.AsyncClient,
        open_ticket: OpenTicket,
    ) -> None:
        """Ids restart at 1 per tenant — separate schemas, separate sequences.

        A shared table with a tenant filter would produce 1 and 2 instead, so
        this distinguishes real schema isolation from application-level
        filtering.
        """
        a = await open_ticket("iso-alpha", "first for alpha")
        b = await open_ticket("iso-beta", "first for beta")
        assert a["id"] == b["id"] == 1


class TestWriteIsolation:
    async def test_patch_cannot_reach_another_tenant(
        self,
        client: httpx.AsyncClient,
        open_ticket: OpenTicket,
    ) -> None:
        target = await open_ticket("iso-beta", "beta untouched")

        resp = await client.patch(
            f"/tickets/{target['id']}",
            headers=_ALPHA,
            json={"subject": "hijacked"},
        )
        assert resp.status_code == 404

        after = (await client.get(f"/tickets/{target['id']}", headers=_BETA)).json()
        assert after["subject"] == "beta untouched"

    async def test_delete_cannot_reach_another_tenant(
        self,
        client: httpx.AsyncClient,
        open_ticket: OpenTicket,
    ) -> None:
        target = await open_ticket("iso-beta", "beta survives")

        assert (await client.delete(f"/tickets/{target['id']}", headers=_ALPHA)).status_code == 404

        after = await client.get(f"/tickets/{target['id']}", headers=_BETA)
        assert after.status_code == 200
        assert after.json()["subject"] == "beta survives"

    async def test_writes_land_in_the_callers_schema(
        self,
        client: httpx.AsyncClient,
        open_ticket: OpenTicket,
    ) -> None:
        """A write must not appear in the other tenant's view."""
        await open_ticket("iso-alpha", "alpha only")

        beta_rows = (await client.get("/tickets", headers=_BETA)).json()
        assert beta_rows == []


class TestAccessControl:
    async def test_unknown_tenant_is_indistinguishable_from_malformed(
        self, client: httpx.AsyncClient
    ) -> None:
        """A 404 here would confirm which identifiers exist."""
        unknown = await client.get("/tickets", headers={"X-Tenant-ID": "ghost-corp"})
        malformed = await client.get("/tickets", headers={"X-Tenant-ID": "Not A Slug!"})

        assert unknown.status_code == 400
        assert malformed.status_code == 400
        assert unknown.json() == malformed.json()

    async def test_missing_header_is_refused(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/tickets")).status_code == 400

    async def test_suspended_tenant_loses_data_access(
        self,
        client: httpx.AsyncClient,
        open_ticket: OpenTicket,
    ) -> None:
        """Suspension must gate the data, not merely the identity endpoint."""
        await open_ticket("iso-alpha", "before suspension")
        assert (await client.get("/tickets", headers=_ALPHA)).status_code == 200

        assert (await client.post("/admin/tenants/iso-alpha/suspend")).status_code == 200

        assert (await client.get("/tickets", headers=_ALPHA)).status_code == 403
        # The other tenant is unaffected.
        assert (await client.get("/tickets", headers=_BETA)).status_code == 200

    async def test_health_and_admin_bypass_tenancy(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/admin/tenants")).status_code == 200

    async def test_quota_comes_from_the_plan(
        self,
        client: httpx.AsyncClient,
        provision: Provision,
    ) -> None:
        await provision("quota-corp", plan="free")
        body = (await client.get("/me", headers={"X-Tenant-ID": "quota-corp"})).json()
        assert body["tenant"]["identifier"] == "quota-corp"
        assert body["max_users"] == 5
