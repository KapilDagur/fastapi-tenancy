"""
app/workers/main.py — Background worker for async project notifications.

In a real system this would consume from a message queue (Celery, ARQ, etc.).
This demo polls Redis for job keys.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

logger = logging.getLogger(__name__)


async def process_project_created(job: dict) -> None:
    """Simulate sending a welcome email when a project is created."""
    tenant_id  = job["tenant_id"]
    project_id = job["project_id"]
    project_name = job["project_name"]
    logger.info(
        "[worker] Project created — tenant=%s project=%s name=%r",
        tenant_id, project_id, project_name,
    )
    # In production: send email, post to Slack, update analytics, etc.
    await asyncio.sleep(0)  # yield to event loop


async def main() -> None:
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    logging.basicConfig(level=logging.INFO)

    try:
        from redis import asyncio as aioredis
        redis = aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)
        logger.info("Worker started, polling Redis queue: projectr:jobs")

        while True:
            # BLPOP blocks until a job arrives (timeout=5s to allow clean shutdown)
            item = await redis.blpop("projectr:jobs", timeout=5)
            if item:
                _, raw = item
                try:
                    job = json.loads(raw)
                    await process_project_created(job)
                except Exception as exc:
                    logger.error("Job processing failed: %s", exc, exc_info=True)
            else:
                logger.debug("Queue empty, polling again…")

    except ImportError:
        logger.warning("redis package not installed — worker sleeping indefinitely")
        while True:
            await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
