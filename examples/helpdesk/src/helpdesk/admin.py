"""Operator panel: manage the tenant registry from a browser.

Scope
-----
The panel manages the **tenant registry** --- the ``tenants`` table in the
public schema, owned by ``SQLAlchemyTenantStore``.  That is the cross-tenant
control plane: onboard a customer, suspend one, inspect status.

It deliberately does *not* browse per-tenant data.  sqladmin binds to a single
engine and schema, whereas tickets and users live in one schema per customer;
a panel that appeared to list "all tickets" would either show nothing or show
whichever schema happened to be on the ``search_path``.  Per-tenant data is
reached through the tenant-scoped API, authenticated as a user of that tenant.

Why sqladmin
------------
It is SQLAlchemy 2.0 native and Starlette based, so it shares this app's
engine, models and session style.  ``fastapi-admin`` was the obvious
alternative and does not fit: it is built on Tortoise ORM plus the archived
``aioredis``, which imports ``distutils`` and therefore cannot run on Python
3.12+ at all.

.. warning:: Provisioning still belongs to the API

    Creating a tenant row here does **not** create its schema --- that is
    ``manager.register_tenant()``'s job.  The panel is therefore read-mostly by
    design: it can edit and suspend, but ``can_create`` is off so nobody
    produces a registry row with no schema behind it.
"""

import os
import secrets
from typing import Any, ClassVar

from fastapi_tenancy.storage.database import TenantModel
from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

_SESSION_KEY = "helpdesk_operator"


class TenantAdmin(ModelView, model=TenantModel):
    """Registry view over the tenants table.

    .. note:: The URL segment is ``tenant-model``, not ``tenant``

        sqladmin derives ``identity`` from the *mapped class* name, which here
        is the library's ``TenantModel``.  Setting ``identity`` on this view
        does not change it --- sqladmin's metaclass overwrites the attribute.
        Views live at ``/panel/tenant-model/list`` and friends.
    """

    name = "Tenant"
    name_plural = "Tenants"
    icon = "fa-solid fa-building"

    column_list: ClassVar[list[Any]] = [
        TenantModel.identifier,
        TenantModel.name,
        TenantModel.status,
        TenantModel.created_at,
    ]
    column_searchable_list: ClassVar[list[Any]] = [TenantModel.identifier, TenantModel.name]
    column_sortable_list: ClassVar[list[Any]] = [
        TenantModel.identifier,
        TenantModel.status,
        TenantModel.created_at,
    ]
    column_default_sort = ("created_at", True)

    # `database_url` may hold credentials for DATABASE-isolation tenants, so it
    # is kept off every view rather than merely off the list.
    column_details_exclude_list: ClassVar[list[Any]] = [TenantModel.database_url]
    form_excluded_columns: ClassVar[list[Any]] = [TenantModel.database_url]

    # Creating a row here would leave a tenant with no schema; provisioning is
    # `manager.register_tenant()`'s job. Deleting would orphan a live schema.
    can_create = False
    can_delete = False
    can_edit = True
    can_view_details = True


class OperatorAuth(AuthenticationBackend):
    """Single-operator session login.

    Intentionally the simplest thing that is not insecure: a shared operator
    credential compared in constant time, held in a signed session cookie.

    A real deployment should replace this with the identity provider it already
    has (SSO/OIDC) --- the point of the class is that sqladmin's
    ``AuthenticationBackend`` is where that integration goes, not that a shared
    password is good practice.
    """

    def __init__(self, secret_key: str, username: str, password: str) -> None:
        super().__init__(secret_key=secret_key)
        self._username = username
        self._password = password

    async def login(self, request: Request) -> bool:
        """Validate the submitted credentials and start a session."""
        form = await request.form()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))
        # compare_digest on both fields: a plain `==` on the username leaks its
        # length through timing, and short-circuits before the password check.
        ok = secrets.compare_digest(username, self._username) & secrets.compare_digest(
            password, self._password
        )
        if ok:
            request.session.update({_SESSION_KEY: username})
        return bool(ok)

    async def logout(self, request: Request) -> bool:
        """Clear the operator session."""
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> Response | bool:
        """Allow the request when a valid operator session is present."""
        if request.session.get(_SESSION_KEY):
            return True
        return RedirectResponse(request.url_for("admin:login"), status_code=302)


def mount_admin(app: Starlette, engine: AsyncEngine, *, base_url: str = "/panel") -> Admin:
    """Mount the operator panel.

    Args:
        app: The FastAPI application.
        engine: Engine for the **public** schema, where the registry lives.
        base_url: Mount point.  Kept off ``/admin`` so it does not shadow the
            JSON admin API, and it must be listed in the middleware's
            ``excluded_paths`` --- the panel is cross-tenant and has no
            ``X-Tenant-ID``.

    Returns:
        The configured :class:`sqladmin.Admin` instance.
    """
    auth = OperatorAuth(
        secret_key=os.getenv("HELPDESK_SECRET", "test-secret-not-for-production-use"),
        username=os.getenv("HELPDESK_OPERATOR_USER", "operator"),
        password=os.getenv("HELPDESK_OPERATOR_PASSWORD", "operator-dev-password"),
    )
    admin = Admin(
        app=app,
        engine=engine,
        base_url=base_url,
        title="Helpdesk operators",
        authentication_backend=auth,
    )
    admin.add_view(TenantAdmin)
    return admin


__all__ = ["OperatorAuth", "TenantAdmin", "mount_admin"]
