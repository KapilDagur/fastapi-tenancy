"""Request and response models for the helpdesk API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TicketStatus = Literal["open", "pending", "closed"]


class TenantCreate(BaseModel):
    """Payload for provisioning a new customer organisation."""

    identifier: str = Field(
        min_length=3,
        max_length=63,
        description="URL-safe slug, e.g. 'acme-corp'. Becomes the schema name.",
        examples=["acme-corp"],
    )
    name: str = Field(min_length=1, max_length=200, examples=["Acme Corporation"])
    plan: Literal["free", "pro", "enterprise"] = "free"


class TenantOut(BaseModel):
    """Public view of a tenant. Never exposes ``database_url``."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    identifier: str
    name: str
    status: str


class TenantQuota(BaseModel):
    """The caller's own tenant plus its resolved quota."""

    tenant: TenantOut
    max_users: int | None
    rate_limit_per_minute: int
    features_enabled: list[str]


class TicketCreate(BaseModel):
    """Payload for opening a ticket."""

    subject: str = Field(min_length=1, max_length=200)
    body: str = ""
    # A deliberately loose check rather than pydantic's EmailStr, which would
    # pull in the optional `email-validator` dependency just for an example.
    requester_email: str = Field(
        min_length=3,
        max_length=320,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        examples=["ops@acme.test"],
    )


class TicketUpdate(BaseModel):
    """Partial update. Unset fields are left untouched.

    ``exclude_unset`` on the handler side is what makes this a PATCH rather
    than a PUT: omitting ``body`` means "leave it alone", not "clear it".
    """

    subject: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = None
    status: TicketStatus | None = None


class TicketOut(BaseModel):
    """A ticket as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    subject: str
    body: str
    status: TicketStatus
    requester_email: str
    created_at: datetime


__all__ = [
    "TenantCreate",
    "TenantOut",
    "TenantQuota",
    "TicketCreate",
    "TicketOut",
    "TicketStatus",
    "TicketUpdate",
]
