"""LISTEN/NOTIFY wake-ups. Best effort: missed notifications are covered by polling (runner.py)."""

import asyncio
import logging

import psycopg

from app.core.config import settings
from app.repositories.receipt_repository import JOBS_CHANNEL
from app.workers.queue import wait_at_most

log = logging.getLogger(__name__)

RECONNECT_SECONDS = 5


async def listen_for_jobs(wake: asyncio.Event, stopping: asyncio.Event) -> None:
    """Set `wake` on every notification until `stopping` is set, reconnecting on errors."""
    dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    while not stopping.is_set():
        try:
            async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
                await conn.execute(f"LISTEN {JOBS_CHANNEL}")
                async for _ in conn.notifies():
                    wake.set()
        except psycopg.Error as exc:
            log.warning("LISTEN connection lost (%s); polling until it reconnects", exc)
            await wait_at_most(stopping, RECONNECT_SECONDS)
