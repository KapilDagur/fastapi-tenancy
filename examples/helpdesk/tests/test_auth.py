"""Authentication and authorisation, including the cross-tenant boundary.

The test that matters most is
:meth:`TestCrossTenantTokens.test_a_token_is_useless_against_another_tenant`.
Everything else is ordinary auth coverage; that one pins the property the
whole design exists to provide.
"""

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from _helpers import Provision
    import httpx

pytestmark = pytest.mark.e2e

_PASSWORD = "correct-horse-battery-staple"


async def _register(
    client: "httpx.AsyncClient",
    tenant: str,
    email: str,
    password: str = _PASSWORD,
) -> dict[str, Any]:
    resp = await client.post(
        "/auth/register",
        headers={"X-Tenant-ID": tenant},
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201, resp.text
    created: dict[str, Any] = resp.json()
    return created


async def _login(
    client: "httpx.AsyncClient",
    tenant: str,
    email: str,
    password: str = _PASSWORD,
) -> str:
    resp = await client.post(
        "/auth/login",
        headers={"X-Tenant-ID": tenant},
        data={"username": email, "password": password},
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


def _auth(tenant: str, token: str) -> dict[str, str]:
    return {"X-Tenant-ID": tenant, "Authorization": f"Bearer {token}"}


class TestRegistrationAndLogin:
    async def test_register_then_login(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        await provision("auth-corp")
        user = await _register(client, "auth-corp", "agent@auth-corp.example.com")
        assert user["email"] == "agent@auth-corp.example.com"
        assert "hashed_password" not in user

        token = await _login(client, "auth-corp", "agent@auth-corp.example.com")
        assert token

    async def test_login_with_a_wrong_password_is_refused(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        await provision("auth-corp")
        await _register(client, "auth-corp", "agent@auth-corp.example.com")
        resp = await client.post(
            "/auth/login",
            headers={"X-Tenant-ID": "auth-corp"},
            data={"username": "agent@auth-corp.example.com", "password": "wrong-password-here"},
        )
        assert resp.status_code == 400

    async def test_short_passwords_are_rejected(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        await provision("auth-corp")
        resp = await client.post(
            "/auth/register",
            headers={"X-Tenant-ID": "auth-corp"},
            json={"email": "weak@auth-corp.example.com", "password": "short"},
        )
        assert resp.status_code == 400

    async def test_password_may_not_contain_the_email(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        await provision("auth-corp")
        resp = await client.post(
            "/auth/register",
            headers={"X-Tenant-ID": "auth-corp"},
            json={
                "email": "agent@auth-corp.example.com",
                "password": "xx-agent@auth-corp.example.com-xx",
            },
        )
        assert resp.status_code == 400

    async def test_registration_requires_a_tenant(self, client: "httpx.AsyncClient") -> None:
        """Auth routes are tenant-scoped: no header, no registration."""
        resp = await client.post(
            "/auth/register",
            json={"email": "nobody@nowhere.example.com", "password": _PASSWORD},
        )
        assert resp.status_code == 400


class TestProtectedRoutes:
    async def test_tickets_require_authentication(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        """A resolved tenant is not enough — the caller must also be a user."""
        await provision("auth-corp")
        resp = await client.get("/tickets", headers={"X-Tenant-ID": "auth-corp"})
        assert resp.status_code == 401

    async def test_authenticated_agent_can_use_tickets(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        await provision("auth-corp")
        await _register(client, "auth-corp", "agent@auth-corp.example.com")
        token = await _login(client, "auth-corp", "agent@auth-corp.example.com")

        created = await client.post(
            "/tickets",
            headers=_auth("auth-corp", token),
            json={"subject": "printer", "requester_email": "user@auth-corp.example.com"},
        )
        assert created.status_code == 201
        assert created.json()["created_by_id"] is not None

        listed = await client.get("/tickets", headers=_auth("auth-corp", token))
        assert listed.status_code == 200
        assert len(listed.json()) == 1

    async def test_me_reports_the_caller(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        await provision("auth-corp", plan="free")
        await _register(client, "auth-corp", "agent@auth-corp.example.com")
        token = await _login(client, "auth-corp", "agent@auth-corp.example.com")

        body = (await client.get("/me", headers=_auth("auth-corp", token))).json()
        assert body["tenant"]["identifier"] == "auth-corp"
        assert body["user"]["email"] == "agent@auth-corp.example.com"
        assert body["max_users"] == 5

    async def test_garbage_token_is_refused(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        await provision("auth-corp")
        resp = await client.get("/tickets", headers=_auth("auth-corp", "not-a-real-token"))
        assert resp.status_code == 401

    async def test_logout_revokes_the_token(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        """The database strategy exists so this is possible at all.

        A stateless JWT would keep working until it expired.
        """
        await provision("auth-corp")
        await _register(client, "auth-corp", "agent@auth-corp.example.com")
        token = await _login(client, "auth-corp", "agent@auth-corp.example.com")

        assert (await client.get("/tickets", headers=_auth("auth-corp", token))).status_code == 200

        out = await client.post("/auth/logout", headers=_auth("auth-corp", token))
        assert out.status_code == 204

        assert (await client.get("/tickets", headers=_auth("auth-corp", token))).status_code == 401


class TestRoles:
    async def test_agent_cannot_delete_a_ticket(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        await provision("role-corp")
        await _register(client, "role-corp", "agent@role-corp.example.com")
        token = await _login(client, "role-corp", "agent@role-corp.example.com")

        created = await client.post(
            "/tickets",
            headers=_auth("role-corp", token),
            json={"subject": "x", "requester_email": "u@role-corp.example.com"},
        )
        ticket_id = created.json()["id"]

        resp = await client.delete(f"/tickets/{ticket_id}", headers=_auth("role-corp", token))
        assert resp.status_code == 403

        # The agent can still do agent things.
        patched = await client.patch(
            f"/tickets/{ticket_id}",
            headers=_auth("role-corp", token),
            json={"status": "closed"},
        )
        assert patched.status_code == 200

    async def test_admin_can_delete_a_ticket(
        self, client: "httpx.AsyncClient", provision: "Provision", promote: Any
    ) -> None:
        await provision("role-corp")
        await _register(client, "role-corp", "boss@role-corp.example.com")
        await promote("role-corp", "boss@role-corp.example.com")
        token = await _login(client, "role-corp", "boss@role-corp.example.com")

        created = await client.post(
            "/tickets",
            headers=_auth("role-corp", token),
            json={"subject": "x", "requester_email": "u@role-corp.example.com"},
        )
        resp = await client.delete(
            f"/tickets/{created.json()['id']}", headers=_auth("role-corp", token)
        )
        assert resp.status_code == 204

    async def test_new_users_are_agents_not_admins(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        """Self-registration must not be able to mint an admin.

        A ``role`` field accepted from the registration body would be
        privilege escalation in one line of JSON.
        """
        await provision("role-corp")
        resp = await client.post(
            "/auth/register",
            headers={"X-Tenant-ID": "role-corp"},
            json={
                "email": "sneaky@role-corp.example.com",
                "password": _PASSWORD,
                "role": "admin",
                "is_superuser": True,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["role"] == "agent"
        assert resp.json()["is_superuser"] is False


class TestCrossTenantTokens:
    async def test_a_token_is_useless_against_another_tenant(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        """The property the whole auth design exists to guarantee.

        Users live in per-tenant schemas, so a token is only meaningful next
        to the tenant it was issued for.  Presenting Acme's token with
        Globex's header must not authenticate anything: the token row lives in
        Acme's schema and simply is not found when the request resolves to
        Globex.
        """
        await provision("tok-acme")
        await provision("tok-globex")
        await _register(client, "tok-acme", "agent@tok-acme.example.com")
        await _register(client, "tok-globex", "agent@tok-globex.example.com")

        acme_token = await _login(client, "tok-acme", "agent@tok-acme.example.com")

        # Valid where it was issued...
        assert (
            await client.get("/tickets", headers=_auth("tok-acme", acme_token))
        ).status_code == 200

        # ...and worthless anywhere else.
        crossed = await client.get("/tickets", headers=_auth("tok-globex", acme_token))
        assert crossed.status_code == 401, (
            "A token issued for one tenant authenticated against another — "
            "the per-tenant token store is not scoping lookups"
        )

    async def test_same_email_in_two_tenants_are_different_accounts(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        """One person may work for two customers with the same address.

        The accounts are unrelated, and a password change in one has no
        effect on the other.
        """
        await provision("tok-acme")
        await provision("tok-globex")

        shared = "consultant@example.example.com"
        await _register(client, "tok-acme", shared, password=_PASSWORD)
        await _register(client, "tok-globex", shared, password="a-totally-different-pw")

        assert await _login(client, "tok-acme", shared, _PASSWORD)
        assert await _login(client, "tok-globex", shared, "a-totally-different-pw")

        wrong = await client.post(
            "/auth/login",
            headers={"X-Tenant-ID": "tok-globex"},
            data={"username": shared, "password": _PASSWORD},
        )
        assert wrong.status_code == 400

    async def test_suspending_a_tenant_blocks_its_users(
        self, client: "httpx.AsyncClient", provision: "Provision"
    ) -> None:
        """Tenant status outranks a valid user session."""
        await provision("tok-acme")
        await _register(client, "tok-acme", "agent@tok-acme.example.com")
        token = await _login(client, "tok-acme", "agent@tok-acme.example.com")
        assert (await client.get("/tickets", headers=_auth("tok-acme", token))).status_code == 200

        await client.post("/admin/tenants/tok-acme/suspend")

        assert (await client.get("/tickets", headers=_auth("tok-acme", token))).status_code == 403
