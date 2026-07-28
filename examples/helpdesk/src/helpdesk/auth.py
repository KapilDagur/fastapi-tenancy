"""Authentication and authorisation, scoped per tenant.

The security property this module exists to guarantee
-----------------------------------------------------
Users live in each tenant's own schema, so two customers can hold rows with
the same primary key.  A token is just a claim about a user id.  If the token
were not bound to a tenant, a token minted for Acme would be presented with
``X-Tenant-ID: globex`` and the lookup would happen **in Globex's schema** ---
authenticating as whichever Globex user happened to share that id.

**The token store is per-tenant, and that is what binds the token.**
``AccessToken`` lives in the tenant's schema, so a token row issued for Acme
does not exist when the request resolves to Globex: the lookup finds nothing
and the request is rejected as unauthenticated.  There is no shared token
table to get this wrong in.

This is worth stating plainly because the tempting alternative is worse.  A
"tenant" claim checked against the request would be **no control at all** if
the claim came from a client-supplied header --- the client controls that.  It
only helps inside a *signed* token, and even then it is second to the fact
that the lookup itself is scoped.  ``test_auth.py`` proves the property
directly: a valid Acme token presented with Globex's header is refused.

The consequence to remember: if the token table is ever moved to a shared
schema for convenience, this guarantee disappears silently.  Keep it per
tenant.

Why a database strategy rather than plain JWT
---------------------------------------------
A stateless JWT cannot be revoked.  Suspending a customer, firing an employee,
or leaking a token all need immediate effect.  Rows in a per-tenant table give
that, and cost one indexed primary-key lookup per request.
"""

from collections.abc import AsyncGenerator
import os
from typing import Annotated
import uuid

from fastapi import Depends, HTTPException, status
from fastapi_tenancy.core.types import Tenant
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, models, schemas
from fastapi_users.authentication import AuthenticationBackend, BearerTransport
from fastapi_users.authentication.strategy.db import AccessTokenDatabase, DatabaseStrategy
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from helpdesk.models import AccessToken, Role, User

#: Lifetime of an issued token.  Short by helpdesk standards: agents keep a tab
#: open all day, so this trades a daily re-login for a small revocation window.
TOKEN_LIFETIME_SECONDS = 60 * 60 * 8

#: Separator that cannot appear in a tenant identifier (the slug grammar is
#: ``[a-z0-9-]``), so splitting the stored value is unambiguous.
_TENANT_SEP = "|"


def _secret() -> str:
    """Return the token-signing secret.

    Raises:
        RuntimeError: When running outside tests without a configured secret.
    """
    secret = os.getenv("HELPDESK_SECRET")
    if secret:
        return secret
    if os.getenv("PYTEST_CURRENT_TEST"):
        return "test-secret-not-for-production-use-abcdefghijklmnop"
    msg = "HELPDESK_SECRET must be set. Generate one with `openssl rand -hex 32`."
    raise RuntimeError(msg)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    """Password policy and registration hooks for one tenant's users."""

    reset_password_token_secret = ""  # set per instance below
    verification_token_secret = ""

    def __init__(self, user_db: SQLAlchemyUserDatabase[User, uuid.UUID]) -> None:
        super().__init__(user_db)
        self.reset_password_token_secret = _secret()
        self.verification_token_secret = _secret()

    async def validate_password(self, password: str, user: schemas.UC | models.UP) -> None:
        """Reject passwords that are trivially weak.

        Deliberately minimal — length is the property that matters most and the
        only one worth hard-coding.  Real deployments should check against a
        breached-password corpus (e.g. Have I Been Pwned's k-anonymity API)
        rather than inventing composition rules, which push users toward
        predictable substitutions.

        Args:
            password: The candidate password.
            user: The user being created or updated.

        Raises:
            InvalidPasswordException: When the password is too short or
                contains the account's e-mail address.
        """
        from fastapi_users import InvalidPasswordException

        if len(password) < 12:
            raise InvalidPasswordException("Password must be at least 12 characters.")
        email = getattr(user, "email", "") or ""
        if email and email.lower() in password.lower():
            raise InvalidPasswordException("Password must not contain your e-mail address.")


def make_auth(
    get_tenant_db: object,
) -> tuple[FastAPIUsers[User, uuid.UUID], AuthenticationBackend[User, uuid.UUID]]:
    """Build the fastapi-users wiring on top of a tenant-scoped session.

    Every dependency below resolves *per request*, which is what makes this
    multi-tenant: the session, the user table, the token table and the strategy
    all belong to whichever tenant the middleware resolved.

    Args:
        get_tenant_db: The tenant-scoped session dependency produced by
            ``make_tenant_db_dependency(manager)``.

    Returns:
        The ``FastAPIUsers`` helper and the authentication backend.
    """

    async def get_user_db(
        session: Annotated[AsyncSession, Depends(get_tenant_db)],
    ) -> AsyncGenerator[SQLAlchemyUserDatabase[User, uuid.UUID], None]:
        yield SQLAlchemyUserDatabase(session, User)

    async def get_access_token_db(
        session: Annotated[AsyncSession, Depends(get_tenant_db)],
    ) -> AsyncGenerator[AccessTokenDatabase[AccessToken], None]:
        yield SQLAlchemyAccessTokenDatabase(session, AccessToken)

    async def get_user_manager(
        user_db: Annotated[SQLAlchemyUserDatabase[User, uuid.UUID], Depends(get_user_db)],
    ) -> AsyncGenerator[UserManager, None]:
        yield UserManager(user_db)

    def get_strategy(
        access_token_db: Annotated[AccessTokenDatabase[AccessToken], Depends(get_access_token_db)],
    ) -> DatabaseStrategy[User, uuid.UUID, AccessToken]:
        return DatabaseStrategy(access_token_db, lifetime_seconds=TOKEN_LIFETIME_SECONDS)

    backend = AuthenticationBackend(
        name="bearer-db",
        transport=BearerTransport(tokenUrl="auth/login"),
        get_strategy=get_strategy,
    )
    users = FastAPIUsers[User, uuid.UUID](get_user_manager, [backend])
    return users, backend


def make_user_dependencies(
    users: FastAPIUsers[User, uuid.UUID],
) -> tuple[object, object, object]:
    """Build the authn/authz dependencies used by route handlers.

    Args:
        users: The configured ``FastAPIUsers`` helper.

    Returns:
        ``(current_user, require_agent, require_admin)``.
    """
    current_active_user = users.current_user(active=True)

    async def current_user(
        user: Annotated[User, Depends(current_active_user)],
    ) -> User:
        """Return the authenticated, active user for this tenant.

        No explicit tenant check is needed here: ``current_active_user``
        resolved the token against the *tenant's own* ``AccessToken`` table,
        so a token issued elsewhere never resolves in the first place.  See
        the module docstring.
        """
        return user

    def require_role(*allowed: Role) -> object:
        async def _guard(user: Annotated[User, Depends(current_user)]) -> User:
            if user.role not in {r.value for r in allowed}:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    f"Requires role: {', '.join(sorted(r.value for r in allowed))}",
                )
            return user

        return _guard

    return current_user, require_role(Role.AGENT, Role.ADMIN), require_role(Role.ADMIN)


def tenant_of(user: User, tenant: Tenant) -> str:
    """Return the tenant-qualified identity of *user*, for logging.

    Args:
        user: The authenticated user.
        tenant: The resolved tenant.

    Returns:
        A string like ``"acme-corp|agent@acme.test"``.
    """
    return f"{tenant.identifier}{_TENANT_SEP}{user.email}"


__all__ = [
    "TOKEN_LIFETIME_SECONDS",
    "UserManager",
    "make_auth",
    "make_user_dependencies",
    "tenant_of",
]
