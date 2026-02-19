"""ORM models for the Invoicer application."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    BigInteger, Column, DateTime, ForeignKey, Numeric,
    String, Text, text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class InvoiceStatus(str, Enum):
    DRAFT     = "draft"
    SENT      = "sent"
    PAID      = "paid"
    OVERDUE   = "overdue"
    CANCELLED = "cancelled"


class Customer(Base):
    """A customer belongs to exactly one tenant schema."""
    __tablename__ = "customers"

    id         = Column(BigInteger, primary_key=True, autoincrement=True)
    name       = Column(String(255), nullable=False)
    email      = Column(String(255), nullable=False, unique=True)
    company    = Column(String(255))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    invoices   = relationship("Invoice", back_populates="customer", lazy="select")


class Invoice(Base):
    """An invoice belongs to a customer in the same tenant schema."""
    __tablename__ = "invoices"

    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    customer_id = Column(BigInteger, ForeignKey("customers.id"), nullable=False)
    number      = Column(String(50), nullable=False, unique=True)
    status      = Column(String(20), nullable=False, default=InvoiceStatus.DRAFT)
    amount      = Column(Numeric(12, 2), nullable=False)
    currency    = Column(String(3), nullable=False, default="USD")
    due_date    = Column(DateTime(timezone=True))
    notes       = Column(Text, default="")
    created_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    customer    = relationship("Customer", back_populates="invoices")


class LineItem(Base):
    """A line item on an invoice."""
    __tablename__ = "line_items"

    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    invoice_id  = Column(BigInteger, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False)
    description = Column(String(500), nullable=False)
    quantity    = Column(Numeric(10, 2), nullable=False, default=1)
    unit_price  = Column(Numeric(12, 2), nullable=False)
