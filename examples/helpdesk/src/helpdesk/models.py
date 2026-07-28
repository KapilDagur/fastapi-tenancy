"""Per-tenant SQLAlchemy models for the helpdesk example.

These tables live **inside each tenant's schema**, not in the shared public
schema.  Note what is *absent*: there is no ``tenant_id`` column anywhere.
Under SCHEMA isolation the schema boundary is the isolation mechanism, so the
application never filters by tenant and therefore cannot forget to.

The tenant registry itself (``TenantModel``) is a separate table owned by
``SQLAlchemyTenantStore`` and lives in the public schema.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for per-tenant tables.

    ``Base.metadata`` is handed to ``manager.register_tenant(app_metadata=...)``,
    which creates every table below inside the newly provisioned tenant schema.
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

    def __repr__(self) -> str:
        """Return a short debug representation."""
        return f"<Ticket id={self.id} status={self.status!r} subject={self.subject!r}>"


__all__ = ["Base", "Ticket"]
