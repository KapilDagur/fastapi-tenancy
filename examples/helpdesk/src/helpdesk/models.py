"""Per-tenant SQLAlchemy models for the helpdesk example.

These tables live **inside each tenant's schema**, not in the shared public
schema.  Note what is *absent*: there is no ``tenant_id`` column anywhere.
Under SCHEMA isolation the schema boundary is the isolation mechanism, so the
application never filters by tenant and therefore cannot forget to.

Users live here too, which is the right call for B2B: an account belongs to one
customer organisation and has no meaning outside it.  It also means dropping a
tenant's schema removes its accounts with it, and that a foreign key from a
ticket to its author can only ever point at a user of the same tenant --- the
database itself refuses a cross-tenant reference.

The tenant registry (``TenantModel``) is a separate table owned by
``SQLAlchemyTenantStore`` and lives in the public schema.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Optional
import uuid

from fastapi_users_db_sqlalchemy import SQLAlchemyBaseUserTableUUID
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyBaseAccessTokenTableUUID
from fastapi_users_db_sqlalchemy.generics import GUID
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for per-tenant tables.

    ``Base.metadata`` is handed to ``manager.register_tenant(app_metadata=...)``,
    which creates every table below inside the newly provisioned tenant schema.
    """


class Role(StrEnum):
    """What a user may do *inside* their own tenant.

    Deliberately coarse.  Authorisation here is within a tenant --- the tenant
    boundary itself is enforced by the schema, so no role can ever grant access
    to another customer's data.
    """

    AGENT = "agent"
    """Handle tickets: create, read, update."""

    ADMIN = "admin"
    """Everything an agent can do, plus delete tickets."""


class User(SQLAlchemyBaseUserTableUUID, Base):
    """A person who logs in, scoped to one tenant.

    Inherits ``id`` / ``email`` / ``hashed_password`` / ``is_active`` /
    ``is_superuser`` / ``is_verified`` from fastapi-users.

    ``is_superuser`` is *tenant-local* and grants nothing across tenants;
    :attr:`role` carries in-tenant authorisation so that distinction stays
    explicit rather than implied.
    """

    role: Mapped[str] = mapped_column(String(20), nullable=False, default=Role.AGENT.value)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:
        """Return a short debug representation."""
        return f"<User {self.email!r} role={self.role!r}>"


class AccessToken(SQLAlchemyBaseAccessTokenTableUUID, Base):
    """Server-side token store, one table per tenant.

    Backs the database auth strategy, which allows tokens to be **revoked** ---
    a stateless JWT cannot be.  Because the table lives in the tenant's schema,
    a token row is only ever visible to the tenant it was issued for.
    """


class Ticket(Base):
    """A support ticket belonging to exactly one tenant.

    Table names are deliberately unqualified.  PostgreSQL resolves them through
    the per-request ``search_path`` that ``SchemaIsolationProvider`` sets, so
    the same mapped class serves every tenant.
    """

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open", index=True)
    requester_email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(  # noqa: UP045
        GUID, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:
        """Return a short debug representation."""
        return f"<Ticket id={self.id} status={self.status!r} subject={self.subject!r}>"


__all__ = ["AccessToken", "Base", "Role", "Ticket", "User"]
