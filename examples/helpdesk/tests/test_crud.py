"""Full CRUD over the tenant-scoped ticket resource."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from _helpers import OpenTicket, Provision, SignIn
    import httpx

pytestmark = pytest.mark.e2e


@pytest.fixture
async def acme(provision: Provision, sign_in: SignIn) -> dict[str, str]:
    """Provision a tenant and return admin headers for it.

    Admin rather than agent so one fixture covers every verb including
    delete; the agent/admin split itself is exercised in ``test_auth.py``.
    """
    await provision("crud-corp")
    return await sign_in("crud-corp", role="admin")


class TestCreate:
    async def test_create_returns_the_persisted_ticket(
        self, client: httpx.AsyncClient, acme: dict[str, str]
    ) -> None:
        resp = await client.post(
            "/tickets",
            headers=acme,
            json={
                "subject": "Printer on fire",
                "body": "It is quite warm.",
                "requester_email": "ops@acme.test",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"] > 0
        assert body["subject"] == "Printer on fire"
        assert body["status"] == "open"  # server-side default
        assert body["created_at"]

    async def test_create_rejects_a_malformed_email(
        self, client: httpx.AsyncClient, acme: dict[str, str]
    ) -> None:
        resp = await client.post(
            "/tickets",
            headers=acme,
            json={"subject": "x", "requester_email": "not-an-email"},
        )
        assert resp.status_code == 422

    async def test_create_rejects_an_empty_subject(
        self, client: httpx.AsyncClient, acme: dict[str, str]
    ) -> None:
        resp = await client.post(
            "/tickets",
            headers=acme,
            json={"subject": "", "requester_email": "ops@acme.test"},
        )
        assert resp.status_code == 422


class TestRead:
    async def test_get_by_id(
        self, client: httpx.AsyncClient, acme: dict[str, str], open_ticket: OpenTicket
    ) -> None:
        created = await open_ticket("crud-corp", subject="vpn down", headers=acme)
        resp = await client.get(f"/tickets/{created['id']}", headers=acme)
        assert resp.status_code == 200
        assert resp.json() == created

    async def test_get_missing_id_is_404(
        self, client: httpx.AsyncClient, acme: dict[str, str]
    ) -> None:
        assert (await client.get("/tickets/999999", headers=acme)).status_code == 404

    async def test_list_is_newest_first(
        self,
        client: httpx.AsyncClient,
        acme: dict[str, str],
        open_ticket: OpenTicket,
    ) -> None:
        await open_ticket("crud-corp", subject="first", headers=acme)
        await open_ticket("crud-corp", subject="second", headers=acme)
        rows = (await client.get("/tickets", headers=acme)).json()
        assert [r["subject"] for r in rows][:2] == ["second", "first"]

    async def test_list_filters_by_status(
        self,
        client: httpx.AsyncClient,
        acme: dict[str, str],
        open_ticket: OpenTicket,
    ) -> None:
        keep = await open_ticket("crud-corp", subject="stays open", headers=acme)
        closing = await open_ticket("crud-corp", subject="gets closed", headers=acme)
        await client.patch(f"/tickets/{closing['id']}", headers=acme, json={"status": "closed"})

        open_rows = (await client.get("/tickets?ticket_status=open", headers=acme)).json()
        closed_rows = (await client.get("/tickets?ticket_status=closed", headers=acme)).json()

        assert keep["id"] in [r["id"] for r in open_rows]
        assert closing["id"] not in [r["id"] for r in open_rows]
        assert closing["id"] in [r["id"] for r in closed_rows]


class TestUpdate:
    async def test_patch_changes_only_supplied_fields(
        self,
        client: httpx.AsyncClient,
        acme: dict[str, str],
        open_ticket: OpenTicket,
    ) -> None:
        created = await open_ticket(
            "crud-corp", subject="original subject", body="original body", headers=acme
        )

        resp = await client.patch(
            f"/tickets/{created['id']}",
            headers=acme,
            json={"status": "pending"},
        )

        assert resp.status_code == 200
        updated = resp.json()
        assert updated["status"] == "pending"
        # Untouched fields survive — this is a PATCH, not a PUT.
        assert updated["subject"] == "original subject"
        assert updated["body"] == "original body"

    async def test_patch_persists(
        self, client: httpx.AsyncClient, acme: dict[str, str], open_ticket: OpenTicket
    ) -> None:
        created = await open_ticket("crud-corp", subject="before", headers=acme)
        await client.patch(f"/tickets/{created['id']}", headers=acme, json={"subject": "after"})
        fetched = (await client.get(f"/tickets/{created['id']}", headers=acme)).json()
        assert fetched["subject"] == "after"

    async def test_patch_with_no_fields_is_rejected(
        self,
        client: httpx.AsyncClient,
        acme: dict[str, str],
        open_ticket: OpenTicket,
    ) -> None:
        created = await open_ticket("crud-corp", subject="x", headers=acme)
        resp = await client.patch(f"/tickets/{created['id']}", headers=acme, json={})
        assert resp.status_code == 400

    async def test_patch_rejects_an_unknown_status(
        self,
        client: httpx.AsyncClient,
        acme: dict[str, str],
        open_ticket: OpenTicket,
    ) -> None:
        created = await open_ticket("crud-corp", subject="x", headers=acme)
        resp = await client.patch(
            f"/tickets/{created['id']}",
            headers=acme,
            json={"status": "banana"},
        )
        assert resp.status_code == 422

    async def test_patch_missing_id_is_404(
        self, client: httpx.AsyncClient, acme: dict[str, str]
    ) -> None:
        resp = await client.patch("/tickets/999999", headers=acme, json={"status": "closed"})
        assert resp.status_code == 404


class TestDelete:
    async def test_delete_removes_the_ticket(
        self,
        client: httpx.AsyncClient,
        acme: dict[str, str],
        open_ticket: OpenTicket,
    ) -> None:
        created = await open_ticket("crud-corp", subject="delete me", headers=acme)

        resp = await client.delete(f"/tickets/{created['id']}", headers=acme)
        assert resp.status_code == 204

        assert (await client.get(f"/tickets/{created['id']}", headers=acme)).status_code == 404

    async def test_delete_is_not_idempotent_by_design(
        self,
        client: httpx.AsyncClient,
        acme: dict[str, str],
        open_ticket: OpenTicket,
    ) -> None:
        """A second delete 404s — the resource is genuinely gone.

        Pinned so a future change to soft-deletion is a deliberate decision
        rather than an accident.
        """
        created = await open_ticket("crud-corp", subject="delete twice", headers=acme)
        assert (await client.delete(f"/tickets/{created['id']}", headers=acme)).status_code == 204
        assert (await client.delete(f"/tickets/{created['id']}", headers=acme)).status_code == 404

    async def test_delete_missing_id_is_404(
        self, client: httpx.AsyncClient, acme: dict[str, str]
    ) -> None:
        assert (await client.delete("/tickets/999999", headers=acme)).status_code == 404
