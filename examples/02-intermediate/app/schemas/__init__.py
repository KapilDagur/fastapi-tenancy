"""Pydantic request/response schemas for Invoicer."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class CustomerCreate(BaseModel):
    name:    str = Field(..., min_length=1, max_length=255)
    email:   str = Field(..., min_length=5, max_length=255)
    company: str | None = None


class CustomerRead(BaseModel):
    id:         int
    name:       str
    email:      str
    company:    str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class LineItemCreate(BaseModel):
    description: str   = Field(..., min_length=1, max_length=500)
    quantity:    Decimal = Field(default=Decimal("1"), gt=0)
    unit_price:  Decimal = Field(..., gt=0)


class InvoiceCreate(BaseModel):
    customer_id: int
    number:      str         = Field(..., min_length=1, max_length=50)
    amount:      Decimal     = Field(..., gt=0)
    currency:    str         = Field(default="USD", min_length=3, max_length=3)
    due_date:    datetime | None = None
    notes:       str         = Field(default="", max_length=5000)
    line_items:  list[LineItemCreate] = Field(default_factory=list)


class InvoiceRead(BaseModel):
    id:          int
    number:      str
    status:      str
    amount:      Decimal
    currency:    str
    due_date:    datetime | None
    notes:       str
    created_at:  datetime
    customer_id: int

    model_config = {"from_attributes": True}


class InvoiceStatusUpdate(BaseModel):
    status: Literal["draft", "sent", "paid", "overdue", "cancelled"]
