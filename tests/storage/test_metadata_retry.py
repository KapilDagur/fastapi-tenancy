"""Retry-bound tests for ``SQLAlchemyTenantStore.update_metadata`` (finding F4).

The SERIALIZABLE metadata merge *must* retry — without it ``update_metadata``
is non-functional under concurrency, because PostgreSQL correctly aborts one of
two concurrent writers with ``SerializationError`` (pgcode 40001).  What it must
not do is retry **twice**: the public method used to wrap a second identical
loop around ``_update_metadata_pg``'s own, so the effective budget was the
product of the two and could not be read off either site.

The budget is sized by contention: N concurrent writers on one row serialise,
so a writer can lose up to N-1 rounds. ``update_metadata`` documents surviving
20 concurrent patches, so 25 is the floor — cutting it to 5 makes
``test_concurrent_metadata_merges_no_lost_update`` fail against real
PostgreSQL with ``TenancyError``, i.e. a lost update reported as a failure.

These tests pin the attempt count from both sides.  They need no PostgreSQL
server: the dialect is forced so the PG branch is taken, and the failure is
injected at the first statement inside the transaction.  ``asyncio.sleep`` is
stubbed in the exhaustion tests so a full 25-attempt budget costs no wall time.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from fastapi_tenancy.core.exceptions import TenancyError, TenantNotFoundError
from fastapi_tenancy.storage.database import SQLAlchemyTenantStore
from fastapi_tenancy.utils.db_compat import DbDialect

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import TracebackType

    from fastapi_tenancy.core.types import Tenant

_EXPECTED_ATTEMPTS = 25
"""The single retry budget in ``_update_metadata_pg``.

Must stay above the 20-concurrent-writer contract in
``test_postgres_store.py::test_concurrent_metadata_merges_no_lost_update``.
"""


class _SerializationError(Exception):
    """Stands in for asyncpg's ``SerializationError``.

    Detection in the store is by class-name substring precisely so asyncpg
    need not be hard-imported; this class satisfies that check.
    """


class _FakeTransaction:
    """Minimal async CM standing in for ``session.begin()``."""

    async def __aenter__(self) -> _FakeTransaction:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        return False


class _AbortingSession:
    """Session whose every transaction aborts with a serialization failure."""

    def __init__(self) -> None:
        self.attempts = 0

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction()

    async def connection(self, **_kw: Any) -> None:
        self.attempts += 1
        msg = "could not serialize access due to read/write dependencies among transactions"
        raise _SerializationError(msg)


@pytest.fixture
async def pg_store() -> AsyncIterator[SQLAlchemyTenantStore]:
    """A store that takes the PostgreSQL code path without a PostgreSQL server.

    The engine is never connected — every test injects its failure before any
    statement reaches the database.
    """
    store = SQLAlchemyTenantStore("sqlite+aiosqlite:///:memory:")
    store._dialect = DbDialect.POSTGRESQL
    try:
        yield store
    finally:
        await store.close()


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collapse retry back-off so exhaustion tests cost no wall time."""

    async def _instant(_delay: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)


@pytest.mark.unit
class TestUpdateMetadataRetryBound:
    async def test_inner_loop_exhausts_the_documented_budget(
        self,
        pg_store: SQLAlchemyTenantStore,
        no_sleep: None,
    ) -> None:
        """``_update_metadata_pg`` owns the retry budget, then re-raises."""
        session = _AbortingSession()

        with pytest.raises(_SerializationError):
            await pg_store._update_metadata_pg(session, "t-1", {"k": "v"})  # type: ignore[arg-type]

        assert session.attempts == _EXPECTED_ATTEMPTS

    async def test_budget_exceeds_the_documented_concurrency_contract(self) -> None:
        """Guards the number itself, not just that the loop honours it.

        ``update_metadata`` promises 20 concurrent patches all persist. With N
        writers serialising one per round, a writer can lose N-1 rounds — so a
        budget at or below 20 breaks that promise against real PostgreSQL.
        """
        assert _EXPECTED_ATTEMPTS > 20

    async def test_public_method_does_not_add_a_second_loop(
        self,
        pg_store: SQLAlchemyTenantStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """F4: one call into the retrying helper — not 5, which gave 25 total."""
        calls: list[str] = []

        async def _always_aborts(_session: Any, tenant_id: str, _metadata: Any) -> Tenant:
            calls.append(tenant_id)
            raise _SerializationError("could not serialize access")

        monkeypatch.setattr(pg_store, "_update_metadata_pg", _always_aborts)

        with pytest.raises(TenancyError):
            await pg_store.update_metadata("t-1", {"k": "v"})

        assert len(calls) == 1

    async def test_total_attempts_stay_within_the_documented_budget(
        self,
        pg_store: SQLAlchemyTenantStore,
        monkeypatch: pytest.MonkeyPatch,
        no_sleep: None,
    ) -> None:
        """End-to-end attempt count through the public entry point.

        Counts the *statements* attempted rather than the helper calls, so it
        fails if a second loop is reintroduced at either level — the product
        would exceed the budget even though each individual loop looks correct.
        """
        session = _AbortingSession()

        class _Factory:
            async def __aenter__(self) -> _AbortingSession:
                return session

            async def __aexit__(self, *_exc: object) -> bool:
                return False

        # The store calls self._session_factory(); the class *is* that factory.
        monkeypatch.setattr(pg_store, "_session_factory", _Factory)

        with pytest.raises(TenancyError):
            await pg_store.update_metadata("t-1", {"k": "v"})

        assert session.attempts == _EXPECTED_ATTEMPTS

    async def test_tenant_not_found_is_neither_retried_nor_wrapped(
        self,
        pg_store: SQLAlchemyTenantStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A missing tenant is a verdict, not a conflict — surface it immediately."""
        calls: list[str] = []

        async def _not_found(_session: Any, tenant_id: str, _metadata: Any) -> Tenant:
            calls.append(tenant_id)
            raise TenantNotFoundError(identifier=tenant_id)

        monkeypatch.setattr(pg_store, "_update_metadata_pg", _not_found)

        with pytest.raises(TenantNotFoundError):
            await pg_store.update_metadata("t-missing", {"k": "v"})

        assert len(calls) == 1

    async def test_non_serialization_error_is_not_retried(
        self,
        pg_store: SQLAlchemyTenantStore,
    ) -> None:
        """Only serialization conflicts are transient; everything else fails fast."""

        class _Broken(_AbortingSession):
            async def connection(self, **_kw: Any) -> None:
                self.attempts += 1
                raise RuntimeError("connection reset")

        session = _Broken()

        with pytest.raises(RuntimeError, match="connection reset"):
            await pg_store._update_metadata_pg(session, "t-1", {"k": "v"})  # type: ignore[arg-type]

        assert session.attempts == 1
