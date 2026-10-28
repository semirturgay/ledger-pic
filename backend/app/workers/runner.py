"""Worker process: WORKER_CONCURRENCY loops that claim, run and repeat. Uploads wake idle loops through
Postgres NOTIFY; they also poll every WORKER_POLL_SECONDS (jittered), which covers any missed wake-up.
SIGTERM lets running jobs finish."""

import asyncio
import logging
import random
import signal

from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

from app.core.config import settings
from app.core.logging import configure_logging
from app.core.process import make_undumpable
from app.db.session import engine
from app.services.extraction import ReceiptExtractor
from app.workers.extraction_job import prune_usage_log, purge_rejected_uploads, run_job
from app.workers.listener import listen_for_jobs
from app.workers.queue import claim_job, wait_at_most

log = logging.getLogger(__name__)

CLAIM_ERROR_BACKOFF_SECONDS = 5
HOUSEKEEPING_SECONDS = 60 * 60


class Worker:
    def __init__(self, extractor: ReceiptExtractor) -> None:
        self.extractor = extractor
        self.stopping = asyncio.Event()
        self.wake = asyncio.Event()

    async def work_loop(self) -> None:
        while not self.stopping.is_set():
            try:
                job = await claim_job()
            except ProgrammingError as exc:  # e.g. tables missing: a readable hint, not a traceback every 5 s
                log.error("Can't read the queue (%s). Is the database migrated? Run: alembic upgrade head", exc.orig)
                await wait_at_most(self.stopping, CLAIM_ERROR_BACKOFF_SECONDS)
                continue
            except SQLAlchemyError:
                log.exception("Could not claim a job; retrying shortly")
                await wait_at_most(self.stopping, CLAIM_ERROR_BACKOFF_SECONDS)
                continue
            if job is None:
                await wait_at_most(self.wake, settings.worker_poll_seconds * random.uniform(0.8, 1.2))
                self.wake.clear()
                continue
            try:
                await run_job(job, self.extractor)
            except Exception:
                # One bad job must not take the worker down. The lease expires and the job is retried,
                # up to extraction_max_attempts (enforced when it is claimed again).
                log.exception("Job %s failed unexpectedly", job.receipt_id)

    async def housekeeping_loop(self) -> None:
        """Hourly: delete uploads that weren't receipts once the user has had time to see why."""
        while not self.stopping.is_set():
            try:
                await purge_rejected_uploads()
                await prune_usage_log()
            except SQLAlchemyError:
                log.exception("Housekeeping failed; will try again next hour")
            await wait_at_most(self.stopping, HOUSEKEEPING_SECONDS)

    def stop(self) -> None:
        log.info("Shutting down; finishing running jobs")
        self.stopping.set()
        self.wake.set()


async def main() -> None:
    make_undumpable()  # the image-decoding child runs as the same user
    configure_logging(settings.log_level)
    worker = Worker(ReceiptExtractor.from_settings(settings))  # one model client, shared by all loops

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.stop)

    listener = asyncio.create_task(listen_for_jobs(worker.wake, worker.stopping), name="listener")
    loops = [
        asyncio.create_task(worker.work_loop(), name=f"worker-{i}") for i in range(settings.worker_concurrency)
    ]
    loops.append(asyncio.create_task(worker.housekeeping_loop(), name="housekeeping"))
    model = settings.claude_model if settings.extraction_provider == "claude" else settings.gemini_model
    log.info(
        "Worker started: %d concurrent jobs, %s model %s",
        settings.worker_concurrency, settings.extraction_provider, model,
    )

    await asyncio.gather(*loops)  # each loop returns once stopping is set and its current job is done
    listener.cancel()
    await engine.dispose()
    log.info("Worker stopped")
