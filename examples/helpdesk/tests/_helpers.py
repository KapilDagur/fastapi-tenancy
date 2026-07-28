"""Typed call signatures for the fixtures in ``conftest.py``.

Kept in their own module so both test modules can annotate fixture parameters
without importing from ``conftest``.
"""

from __future__ import annotations

from typing import Any, Protocol


class Provision(Protocol):
    """Provision a tenant and return the created record."""

    async def __call__(self, identifier: str, plan: str = ...) -> dict[str, Any]:
        """Create the tenant and assert the API accepted it."""
        ...


class OpenTicket(Protocol):
    """Open a ticket for a tenant and return the created record."""

    async def __call__(self, tenant: str, subject: str, body: str = ...) -> dict[str, Any]:
        """Create the ticket and assert the API accepted it."""
        ...


__all__ = ["OpenTicket", "Provision"]
