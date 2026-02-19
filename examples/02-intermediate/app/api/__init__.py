"""Invoice and customer API routes."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fastapi_tenancy import Tenant, get_current_tenant
from fastapi_tenancy.dependencies import get_tenant_db

from app.models import Customer, Invoice, InvoiceStatus, LineItem
from app.schemas import (
    CustomerCreate, CustomerRead,
    InvoiceCreate, InvoiceRead, InvoiceStatusUpdate,
)

router = APIRouter()

CurrentTenant = Annotated[Tenant, Depends(get_current_tenant)]
TenantSession = Annotated[AsyncSession, Depends(get_tenant_db)]


# ── Customers ─────────────────────────────────────────────────────────────────

@router.get("/customers", response_model=list[CustomerRead])
async def list_customers(session: TenantSession, tenant: CurrentTenant):
    """List all customers for the current tenant's schema."""
    result = await session.execute(select(Customer).order_by(Customer.name))
    return result.scalars().all()


@router.post("/customers", response_model=CustomerRead, status_code=201)
async def create_customer(
    body: CustomerCreate,
    session: TenantSession,
    tenant: CurrentTenant,
):
    existing = await session.execute(
        select(Customer).where(Customer.email == body.email)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")
    customer = Customer(**body.model_dump())
    session.add(customer)
    await session.commit()
    await session.refresh(customer)
    return customer


@router.get("/customers/{customer_id}", response_model=CustomerRead)
async def get_customer(customer_id: int, session: TenantSession, tenant: CurrentTenant):
    result = await session.execute(
        select(Customer).where(Customer.id == customer_id)
    )
    customer = result.scalar_one_or_none()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


# ── Invoices ──────────────────────────────────────────────────────────────────

@router.get("/invoices", response_model=list[InvoiceRead])
async def list_invoices(
    session: TenantSession,
    tenant: CurrentTenant,
    status_filter: str | None = None,
):
    """List invoices, optionally filtered by status."""
    q = select(Invoice).order_by(Invoice.created_at.desc())
    if status_filter:
        q = q.where(Invoice.status == status_filter)
    result = await session.execute(q)
    return result.scalars().all()


@router.post("/invoices", response_model=InvoiceRead, status_code=201)
async def create_invoice(
    body: InvoiceCreate,
    session: TenantSession,
    tenant: CurrentTenant,
):
    # Verify customer exists in this tenant's schema
    customer = await session.get(Customer, body.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    # Check invoice number is unique within this tenant
    dup = await session.execute(
        select(Invoice).where(Invoice.number == body.number)
    )
    if dup.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Invoice number already exists")

    data = body.model_dump(exclude={"line_items"})
    invoice = Invoice(**data)
    session.add(invoice)
    await session.flush()  # get invoice.id without committing

    for li in body.line_items:
        session.add(LineItem(invoice_id=invoice.id, **li.model_dump()))

    await session.commit()
    await session.refresh(invoice)
    return invoice


@router.get("/invoices/{invoice_id}", response_model=InvoiceRead)
async def get_invoice(invoice_id: int, session: TenantSession, tenant: CurrentTenant):
    result = await session.execute(
        select(Invoice)
        .where(Invoice.id == invoice_id)
        .options(selectinload(Invoice.customer))
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.patch("/invoices/{invoice_id}/status", response_model=InvoiceRead)
async def update_invoice_status(
    invoice_id: int,
    body: InvoiceStatusUpdate,
    session: TenantSession,
    tenant: CurrentTenant,
):
    """Update invoice status (draft→sent→paid etc.)."""
    result = await session.execute(
        select(Invoice).where(Invoice.id == invoice_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status == InvoiceStatus.CANCELLED:
        raise HTTPException(status_code=422, detail="Cannot update a cancelled invoice")
    invoice.status = body.status
    await session.commit()
    await session.refresh(invoice)
    return invoice


@router.delete("/invoices/{invoice_id}", status_code=204)
async def delete_invoice(invoice_id: int, session: TenantSession, tenant: CurrentTenant):
    result = await session.execute(
        select(Invoice).where(Invoice.id == invoice_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    await session.delete(invoice)
    await session.commit()
