"""Rate-limit Lua script executed against a **real** Redis server.

Every other rate-limit test mocks ``redis.eval`` and asserts on the host-side
handling of its return value.  That left the script itself completely
untested, which is how a total bypass survived: the deny branch returned the
*pre-state* count, the host checked ``count > limit``, and with ``count ==
limit`` that is False — so the limiter allowed the request.  Because the deny
branch also skips the ``ZADD``, the sorted set never grew and every subsequent
request was allowed too.  A limiter with ``limit=5`` passed 15/15 requests.

These tests therefore run the script for real.  ``fakeredis`` is not a
substitute here: the bug lives in Lua and in the interaction between the
script's return value and the host-side comparison, which is exactly what a
fake would have to reimplement to catch.

Marked ``e2e`` because it starts a Redis container.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import uuid

import pytest

from fastapi_tenancy.manager import TenancyManager
from tests import _services

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.e2e

_LIMIT = 5
_WINDOW = 60


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    """Async Redis client against a live server, flushed per test."""
    aioredis = pytest.importorskip("redis.asyncio", reason="redis extra not installed")
    client = aioredis.from_url(_services.redis_url())
    await client.flushall()
    try:
        yield client
    finally:
        await client.flushall()
        await client.aclose()


async def _run(client: Any, key: str, limit: int = _LIMIT, window: int = _WINDOW) -> int:
    """Execute the script once and return the host-side count."""
    raw = await client.eval(
        TenancyManager._RATE_LIMIT_LUA,
        1,
        key,
        limit,
        window,
        uuid.uuid4().hex,
    )
    return int(raw)


def _allowed(count: int, limit: int = _LIMIT) -> bool:
    """Mirror the host-side decision in ``check_rate_limit`` exactly."""
    return not (count > limit)


class TestRateLimitScriptEnforcesTheLimit:
    async def test_exactly_limit_requests_are_allowed(self, redis_client: Any) -> None:
        """The regression test for the bypass: 15 requests, limit 5, 5 allowed."""
        allowed = 0
        for _ in range(15):
            if _allowed(await _run(redis_client, "k")):
                allowed += 1
        assert allowed == _LIMIT

    async def test_request_at_the_limit_is_denied(self, redis_client: Any) -> None:
        """The exact boundary the old script got wrong.

        With N already in the window the script must report a count that
        satisfies ``count > limit``; returning the pre-state ``N`` does not.
        """
        for _ in range(_LIMIT):
            assert _allowed(await _run(redis_client, "k"))
        assert not _allowed(await _run(redis_client, "k"))

    async def test_denial_is_sticky_not_self_healing(self, redis_client: Any) -> None:
        """Repeated denied requests must stay denied within the window.

        The bypass was self-reinforcing: the deny branch skipped the ZADD, so
        the set never grew past the limit and every later request was allowed.
        """
        for _ in range(_LIMIT):
            await _run(redis_client, "k")
        for _ in range(10):
            assert not _allowed(await _run(redis_client, "k"))

    async def test_counts_are_isolated_per_key(self, redis_client: Any) -> None:
        for _ in range(_LIMIT):
            await _run(redis_client, "tenant-a")
        assert not _allowed(await _run(redis_client, "tenant-a"))
        assert _allowed(await _run(redis_client, "tenant-b"))

    async def test_limit_of_one_denies_the_second_request(self, redis_client: Any) -> None:
        """Smallest meaningful limit — off-by-ones show up here first."""
        assert _allowed(await _run(redis_client, "k", limit=1), limit=1)
        assert not _allowed(await _run(redis_client, "k", limit=1), limit=1)


class TestRateLimitScriptMechanics:
    async def test_members_are_unique_per_request(self, redis_client: Any) -> None:
        """Same-tick requests must not overwrite each other's member."""
        for _ in range(_LIMIT):
            await _run(redis_client, "k")
        assert await redis_client.zcard("k") == _LIMIT

    async def test_key_receives_a_ttl(self, redis_client: Any) -> None:
        await _run(redis_client, "k")
        assert 0 < await redis_client.ttl("k") <= _WINDOW

    async def test_score_comes_from_the_redis_clock(self, redis_client: Any) -> None:
        """The script must timestamp from Redis TIME, not from this host.

        Host clocks skew; a backward jump left stale members counting toward
        the limit and a forward jump evicted live ones early.
        """
        await _run(redis_client, "k")
        secs, micros = await redis_client.time()
        redis_now = secs + micros / 1_000_000
        (_member, score), *_ = await redis_client.zrange("k", 0, -1, withscores=True)
        assert abs(score - redis_now) < 5
