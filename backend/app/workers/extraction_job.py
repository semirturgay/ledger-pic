"""One job: resize the image, ask the model, record the outcome (never raised): needs_review,
failed with a failure_reason, or a scheduled retry for transient model errors."""

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy.exc import DataError

from app.core.config import settings
from app.core.errors import UnsupportedImage
from app.core.logging import request_id_var
from app.db.session import SessionLocal
from app.models import PromptType, Receipt
from app.repositories.receipt_repository import ReceiptRepository
from app.services.extraction import ExtractedReceipt, ExtractionResult, NoLongerWanted, ReceiptExtractor
from app.services.images import NORMALIZED_CONTENT_TYPE, normalize_image
from app.services.llm import ExtractionFailed
from app.services.prompts import PromptStore, prompt_store
from app.services.receipt_service import line_items
from app.workers.queue import Clock, Job, SessionFactory, claim_job, retry_delay, utcnow

log = logging.getLogger(__name__)

NOT_A_RECEIPT = "This image doesn't look like a receipt or invoice."
NOTHING_READABLE = "We couldn't read anything on this receipt. Try a sharper, well-lit photo, or fill it in yourself."
# Retries stay silent only for short waits; a longer one (e.g. a daily quota) fails now so the user can act.
MAX_SILENT_WAIT_SECONDS = 15 * 60


@dataclass
class _Failure:
    message: str
    reason: str
    retryable: bool = False
    retry_after: float | None = None
    timed_out: bool = False


async def run_job(
    job: Job,
    extractor: ReceiptExtractor,
    session_factory: SessionFactory = SessionLocal,
    now: Clock = utcnow,
    prompts: PromptStore = prompt_store,
) -> None:
    """Run one leased job and save the outcome. Never raises for image or model problems."""
    request_id_var.set(f"job-{job.receipt_id.hex[:8]}")
    async with session_factory() as db:
        image = await ReceiptRepository(db).get_image(job.receipt_id)
        if image is None:
            return  # receipt deleted since it was claimed
        data, mime_type, already_normalized = image.data, image.content_type, image.normalized

    # No DB session is open from here until the outcome is saved: this part can take seconds.
    failure: _Failure | None = None
    normalized: bytes | None = None
    if not already_normalized:
        try:
            # Decode/rotate/resize runs in a killable child process with CPU, memory and time limits.
            normalized = await normalize_image(
                data, settings.max_image_edge, settings.image_decode_timeout_seconds, settings.decoder_require_landlock
            )
            data, mime_type = normalized, NORMALIZED_CONTENT_TYPE
        except UnsupportedImage as exc:
            failure = _Failure(exc.message, "image_unreadable")

    result: ExtractionResult | None = None
    prompt_version: int | None = None
    async def still_wanted() -> bool:
        async with session_factory() as db:
            return await db.get(Receipt, job.receipt_id) is not None

    if failure is None:
        try:
            prompt = await prompts.active(PromptType.RECEIPT_EXTRACTION)
            prompt_version = prompt.version
            # Asked once a model slot is free, right before the request: a receipt deleted meanwhile
            # (while decoding or waiting for a slot) is never sent to the provider.
            result = await extractor.extract(data, mime_type, prompt.text, still_wanted)
        except NoLongerWanted:
            return
        except ExtractionFailed as exc:
            failure = _Failure(str(exc), "ai_unavailable", exc.retryable, exc.retry_after, exc.timed_out)
        except Exception:
            # Not swallowed: logged with traceback, then treated as transient so the queue retries it.
            log.exception("Unexpected error extracting receipt %s", job.receipt_id)
            failure = _Failure("Unexpected error during extraction.", "ai_unavailable", retryable=True)

    if result is not None:
        failure = _classify(result.data)

    try:
        await _save_outcome(job, extractor, session_factory, now, result, failure, normalized, prompt_version)
    except DataError:
        # Postgres refused something in the answer. Retrying would get the same answer refused again
        # (and pay for the model call each time), so record it as a failure the user can see instead.
        log.exception("Could not save the outcome for receipt %s", job.receipt_id)
        unsavable = _Failure("The model's answer could not be saved. Enter the details by hand.", "ai_unavailable")
        await _save_outcome(job, extractor, session_factory, now, None, unsavable, normalized, prompt_version)


async def _save_outcome(
    job: Job,
    extractor: ReceiptExtractor,
    session_factory: SessionFactory,
    now: Clock,
    result: ExtractionResult | None,
    failure: _Failure | None,
    normalized: bytes | None,
    prompt_version: int | None,
) -> None:
    async with session_factory() as db:
        repo = ReceiptRepository(db)
        receipt = await repo.get_leased(job.receipt_id, job.lease_token)
        if receipt is None:
            log.info("Dropping result for %s: receipt deleted or lease taken over", job.receipt_id)
            return
        if failure is not None and failure.reason == "image_unreadable" and (stored := await repo.get_image(job.receipt_id)):
            # Nothing can be read from it, so don't keep up to 10 MB of it for the retention period.
            stored.data, stored.size_bytes = b"", 0
        if normalized is not None and (stored := await repo.get_image(job.receipt_id)) is not None:
            # Keep the small version from now on: retries skip resizing, and the original's EXIF is gone.
            stored.data, stored.content_type = normalized, NORMALIZED_CONTENT_TYPE
            stored.size_bytes, stored.normalized = len(normalized), True
        if result is not None:
            receipt.raw_ai_json = result.raw  # kept for debugging, even when the draft is rejected
            receipt.prompt_version = prompt_version
            receipt.extraction_model = extractor.model
        outcome = _record_outcome(receipt, result, failure, now)
        if outcome == "needs_review":
            # A re-photographed receipt has a new hash; same merchant, date, total and currency is a strong hint.
            receipt.possible_duplicate_of = await repo.find_likely_duplicate(receipt)
        receipt.lease_token = None
        receipt.lease_expires_at = None
        attempt = receipt.extraction_attempts
        await db.commit()
    log.info("Receipt %s attempt %d: %s", job.receipt_id, attempt, outcome)


async def run_due_jobs(
    extractor: ReceiptExtractor,
    session_factory: SessionFactory = SessionLocal,
    now: Clock = utcnow,
) -> int:
    """Process every job that is due right now, one after another. Returns how many ran."""
    count = 0
    while job := await claim_job(session_factory, now):
        await run_job(job, extractor, session_factory, now)
        count += 1
    return count


async def purge_rejected_uploads(session_factory: SessionFactory = SessionLocal, now: Clock = utcnow) -> int:
    """Not-a-receipt and undecodable uploads are kept briefly (so the user sees why), then deleted."""
    async with session_factory() as db:
        deleted = await ReceiptRepository(db).purge_rejected(now() - dt.timedelta(days=settings.rejected_upload_retention_days))
        await db.commit()
    if deleted:
        log.info("Purged %d rejected uploads", deleted)
    return deleted


async def prune_usage_log(session_factory: SessionFactory = SessionLocal, now: Clock = utcnow) -> int:
    """Daily quotas look back 24 hours; older usage rows are no longer needed."""
    async with session_factory() as db:
        deleted = await ReceiptRepository(db).prune_usage(now() - dt.timedelta(days=2))
        await db.commit()
    return deleted


def _classify(draft: ExtractedReceipt) -> _Failure | None:
    """A valid answer can still be useless: don't hand the user an empty draft to review."""
    if not draft.is_receipt:
        return _Failure(NOT_A_RECEIPT, "not_a_receipt")
    if draft.is_empty:
        return _Failure(NOTHING_READABLE, "nothing_readable")
    return None


def _record_outcome(
    receipt: Receipt, result: ExtractionResult | None, failure: _Failure | None, now: Clock
) -> str:
    if failure is None:
        assert result is not None
        _apply_draft(receipt, result.data)
        receipt.extraction_error = result.error
        receipt.failure_reason = None
        receipt.next_attempt_at = None
        receipt.status = "needs_review"
        return "needs_review"

    attempt, limit = receipt.extraction_attempts, settings.extraction_max_attempts
    # A timeout may be the image's own doing (it makes the model write on and on): one more try, not
    # five, each of which would hold a shared model slot for the whole timeout.
    if failure.timed_out:
        limit = min(limit, 2)
    if failure.retryable and attempt < limit and (failure.retry_after or 0) <= MAX_SILENT_WAIT_SECONDS:
        delay = retry_delay(attempt, failure.retry_after)
        receipt.next_attempt_at = now() + delay
        receipt.queued_at = receipt.next_attempt_at  # behind everything uploaded while it waits
        return f"retry in {delay.total_seconds():.0f}s ({failure.message})"

    receipt.status = "failed"
    receipt.failure_reason = failure.reason
    receipt.next_attempt_at = None
    gave_up = f" Gave up after {attempt} attempts." if failure.retryable and attempt >= limit else ""
    receipt.extraction_error = f"{failure.message}{gave_up}"
    return f"failed ({failure.reason})"


def _apply_draft(receipt: Receipt, draft: ExtractedReceipt) -> None:
    receipt.merchant = draft.merchant
    receipt.purchased_at = draft.purchased_at
    receipt.currency = draft.currency
    receipt.category = draft.category
    receipt.tax = draft.tax
    receipt.total = draft.total
    receipt.line_items = line_items(draft.line_items)
