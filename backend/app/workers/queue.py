import asyncio
import contextlib

"""The extraction queue: a receipt in `processing` is a job, leased via its lease columns."""

import datetime as dt
import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionLocal
from app.repositories.receipt_repository import ReceiptRepository

Clock = Callable[[], dt.datetime]
SessionFactory = Callable[[], AsyncSession]


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def lease_duration() -> dt.timedelta:
    # Longer than the slowest possible job: decoding (one at a time per process, so possibly behind every
    # other loop's), then two model calls (validation retry), each with one SDK retry, plus waiting for a
    # model slot when there are more worker loops than slots (AI_MAX_CONCURRENCY below WORKER_CONCURRENCY).
    waits = -(-settings.worker_concurrency // max(settings.ai_max_concurrency, 1))  # ceiling division
    decoding = settings.image_decode_timeout_seconds * settings.worker_concurrency
    model = (settings.ai_timeout_seconds * 4 + 60) * waits  # + retry delays, DB
    return dt.timedelta(seconds=decoding + model)


def retry_delay(attempt: int, requested: float | None = None) -> dt.timedelta:
    """Backoff that grows 4x per attempt: ~30s, 2m, 8m, 32m, capped at 1h (5 tries span ~40 min,
    enough to ride out a rate-limit window). Never shorter than the wait the service asked for."""
    base = min(30 * 4 ** (attempt - 1), 3600) * random.uniform(0.8, 1.2)
    return dt.timedelta(seconds=max(base, requested or 0))


@dataclass(frozen=True)
class Job:
    receipt_id: uuid.UUID
    lease_token: uuid.UUID


async def claim_job(session_factory: SessionFactory = SessionLocal, now: Clock = utcnow) -> Job | None:
    """Lease the next due job, or return None if nothing is due."""
    async with session_factory() as db:
        claimed = await ReceiptRepository(db).claim_next_job(now(), lease_duration(), settings.extraction_max_attempts)
        await db.commit()  # releases the row lock; the lease now protects the job
    return Job(*claimed) if claimed else None


async def wait_at_most(event: asyncio.Event, seconds: float) -> None:
    """Return when `event` is set or after `seconds`, whichever comes first."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(event.wait(), timeout=seconds)
