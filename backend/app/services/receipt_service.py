import asyncio
import base64
import binascii
import datetime as dt
import hashlib
import uuid
from collections.abc import Iterable
from decimal import Decimal
from typing import Protocol

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import (
    DuplicateUpload,
    EmptyUpload,
    IdempotencyKeyReused,
    ImageTooLarge,
    InvalidCursor,
    InvalidStateTransition,
    NotFound,
    QuotaExceeded,
)
from app.core.money import to_money
from app.core.rate_limit import upload_limiter
from app.models import LineItem, Receipt, ReceiptImage, User
from app.repositories.receipt_repository import ReceiptRepository
from app.schemas.receipt import CategoryTotal, CurrencyTotal, ReceiptUpdate, SummaryOut
from app.services.images import inspect_image


class ReceiptService:
    """Receipt use cases. Each public method is one unit of work and commits at most once."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.receipts = ReceiptRepository(db)

    async def create_from_upload(
        self, user: User, raw: bytes, idempotency_key: str | None = None
    ) -> tuple[Receipt, bool]:
        """Store the original image and queue extraction. Returns (receipt, replayed) for Idempotency-Key retries."""
        self._burst(user, "upload", "uploads")  # before hashing or parsing anything
        if not raw:
            raise EmptyUpload("Uploaded file is empty")
        if len(raw) > settings.max_upload_bytes:
            raise ImageTooLarge("Image is larger than the upload limit")
        sha256 = hashlib.sha256(raw).hexdigest()

        if earlier := await self._earlier_upload(user, idempotency_key, sha256):
            return earlier, True
        # Header check only (the worker decodes); done before the quota locks so they're held briefly.
        content_type = await asyncio.to_thread(inspect_image, raw)
        await self._check_upload_quota(user, len(raw))
        receipt = Receipt(
            user_id=user.id,
            status="processing",
            queued_at=await self.receipts.fair_queue_slot(user.id),
            idempotency_key=idempotency_key,
            upload_sha256=sha256,
            image=ReceiptImage(content_type=content_type, size_bytes=len(raw), data=raw),
        )
        self.receipts.add(receipt)
        self.receipts.record_usage(user.id, "upload")
        await self.receipts.notify_job_queued()
        try:
            await self.db.commit()
        except IntegrityError:
            # Two requests with the same key or the same file raced; the other one won.
            await self.db.rollback()
            if earlier := await self._earlier_upload(user, idempotency_key, sha256):
                return earlier, True
            raise
        return await self.get(user, receipt.id), False

    async def get(self, user: User, receipt_id: uuid.UUID) -> Receipt:
        return await self._owned(user, receipt_id)

    async def list(self, user: User, limit: int, cursor: str | None = None) -> tuple[list[Receipt], str | None]:
        """A page of the user's receipts, newest first, and the cursor for the next page (None at the end)."""
        rows = await self.receipts.list_for_user(user.id, limit + 1, decode_cursor(cursor) if cursor else None)
        page = rows[:limit]
        more = len(rows) > limit
        return page, encode_cursor(page[-1].created_at, page[-1].id) if more else None

    async def get_image(self, user: User, receipt_id: uuid.UUID) -> ReceiptImage:
        image = await self.receipts.get_image_for_user(user.id, receipt_id)
        if image is None:
            raise NotFound("Receipt not found")
        if not image.data:
            raise NotFound("This photo couldn't be read, so it wasn't kept")
        return image

    async def confirm(self, user: User, receipt_id: uuid.UUID, update: ReceiptUpdate) -> Receipt:
        """Store the user's corrected record as the source of truth and mark it confirmed."""
        receipt = await self._owned(user, receipt_id, lock=True)
        if receipt.status == "processing":
            raise InvalidStateTransition("Extraction is still running")
        receipt.merchant = update.merchant
        receipt.purchased_at = update.purchased_at
        receipt.currency = update.currency
        receipt.category = update.category
        receipt.tax = update.tax
        receipt.total = update.total
        receipt.line_items = line_items(update.line_items)
        receipt.extraction_error = None
        receipt.failure_reason = None
        receipt.status = "confirmed"
        receipt.possible_duplicate_of = await self.receipts.find_likely_duplicate(receipt)
        await self.db.commit()
        return await self.get(user, receipt_id)

    async def request_extraction(self, user: User, receipt_id: uuid.UUID) -> Receipt:
        """Re-run the model on an unconfirmed receipt. Confirmed data is never overwritten by AI output."""
        receipt = await self._owned(user, receipt_id, lock=True)
        if receipt.status not in ("failed", "needs_review"):
            raise InvalidStateTransition(f"Cannot re-extract a receipt that is {receipt.status}")
        if receipt.failure_reason == "image_unreadable":
            # The photo itself couldn't be decoded (and isn't kept): reading it again can't help.
            raise InvalidStateTransition("This photo couldn't be read. Upload a clearer one or enter the details by hand.")
        # Each re-run is a paid model call, so it is metered like an upload, and it joins the back of the queue.
        self._burst(user, "extract", "re-runs")
        await self._check_daily(user, "extract", self._daily(user, settings.reextracts_per_day, settings.new_account_reextracts_per_day), "re-runs")
        self.receipts.record_usage(user.id, "extract")
        receipt.queued_at = await self.receipts.fair_queue_slot(user.id)
        receipt.status = "processing"
        receipt.extraction_error = None
        receipt.failure_reason = None
        receipt.extraction_attempts = 0
        receipt.next_attempt_at = None
        await self.receipts.notify_job_queued()
        await self.db.commit()
        return await self.get(user, receipt_id)

    async def delete(self, user: User, receipt_id: uuid.UUID) -> None:
        await self.receipts.delete(await self._owned(user, receipt_id))
        await self.db.commit()

    async def summary(self, user: User, month: str | None) -> SummaryOut:
        """Totals from stored, confirmed receipts only. Drafts and raw AI output never count."""
        month = month or dt.date.today().strftime("%Y-%m")
        year, month_number = map(int, month.split("-"))
        start = dt.date(year, month_number, 1)
        end = dt.date(year + month_number // 12, month_number % 12 + 1, 1)

        def currency_totals(rows: list[tuple[str, Decimal, int]]) -> list[CurrencyTotal]:
            return [CurrencyTotal(currency=c, total=to_money(t), count=n) for c, t, n in rows]

        def category_totals(rows: list[tuple[str, str, Decimal]]) -> list[CategoryTotal]:
            return [CategoryTotal(currency=c, category=cat, total=to_money(t)) for c, cat, t in rows]

        # One session can't run queries concurrently, so these are sequential awaits.
        month_rows = await self.receipts.confirmed_totals_by_currency(user.id, start, end)
        category_rows = await self.receipts.confirmed_totals_by_category(user.id, start, end)
        all_time_rows = await self.receipts.confirmed_totals_by_currency(user.id)
        all_time_category_rows = await self.receipts.confirmed_totals_by_category(user.id)
        pending = await self.receipts.count_unconfirmed(user.id)
        return SummaryOut(
            month=month,
            month_totals=currency_totals(month_rows),
            month_by_category=category_totals(category_rows),
            all_time_totals=currency_totals(all_time_rows),
            all_time_by_category=category_totals(all_time_category_rows),
            pending_review_count=pending,
        )

    async def _check_upload_quota(self, user: User, size: int) -> None:
        """Uploads are for receipts, not photo storage: cap the rate, the daily count and the bytes kept."""
        per_day = self._daily(user, settings.uploads_per_day, settings.new_account_uploads_per_day)
        await self._check_daily(user, "upload", per_day, "uploads", upload_size=size)

    @staticmethod
    def _is_new(user: User) -> bool:
        return dt.datetime.now(dt.UTC) - user.created_at < dt.timedelta(hours=settings.new_account_hours)

    @classmethod
    def _daily(cls, user: User, regular: int, new_account: int) -> int:
        return new_account if cls._is_new(user) else regular

    @staticmethod
    def _burst(user: User, kind: str, noun: str) -> None:
        if not upload_limiter.allow(f"{kind}:{user.id}", settings.uploads_per_minute, 60):
            raise QuotaExceeded(f"Too many {noun} at once. Wait a minute and try again.")

    async def _check_daily(self, user: User, kind: str, per_day: int, noun: str, upload_size: int | None = None) -> None:
        now = dt.datetime.now(dt.UTC)
        since = now - dt.timedelta(days=1)
        try:
            await self.receipts.lock_quota(user.id)
            if await self.receipts.count_usage_since(user.id, kind, since) >= per_day:
                raise QuotaExceeded(f"Daily limit of {per_day} {noun} reached. Try again tomorrow.")
            limit = settings.image_storage_limit_mb * 1024 * 1024
            if upload_size is not None and await self.receipts.stored_image_bytes(user.id) + upload_size > limit:
                raise QuotaExceeded("Storage limit reached. Delete receipts you no longer need to upload more.")
            await self.receipts.lock_quota(None)  # the global counts too, so simultaneous users can't overshoot
            trusted_since = now - dt.timedelta(days=settings.trusted_account_days)
            trusted = user.created_at <= trusted_since
            if not trusted:
                young = await self.receipts.count_new_account_usage_since(since, trusted_since)
                if young >= settings.untrusted_accounts_jobs_per_day_total:
                    raise QuotaExceeded("New accounts have reached today's processing limit. Try again tomorrow.")
            total = await self.receipts.count_all_usage_since(since)
            if total >= settings.ai_jobs_per_day_total and not (
                # Established accounts keep a small guaranteed allowance even when the shared pool is used
                # up (e.g. by many accounts left to age), so nobody can stop their receipts being read;
                # a hard ceiling above it still bounds the spend.
                trusted
                and total < settings.ai_jobs_per_day_hard_total
                and await self.receipts.count_user_usage_since(user.id, since) < settings.guaranteed_jobs_per_day
            ):
                raise QuotaExceeded("LedgerPic has reached today's processing limit. Try again tomorrow.")
        except OperationalError as exc:  # lock_timeout: too many uploads waiting at once
            raise QuotaExceeded("LedgerPic is busy. Try again in a moment.") from exc

    async def _earlier_upload(self, user: User, idempotency_key: str | None, sha256: str) -> Receipt | None:
        """The receipt a retry with the same Idempotency-Key should get back (if it's the same file).
        Otherwise, if this exact photo was uploaded before, refuse it as a duplicate."""
        if idempotency_key and (earlier := await self.receipts.get_by_idempotency_key(user.id, idempotency_key)):
            if earlier.upload_sha256 != sha256:
                raise IdempotencyKeyReused("This Idempotency-Key was already used for a different file")
            return earlier
        if duplicate := await self.receipts.get_by_sha256(user.id, sha256):
            raise DuplicateUpload("You already uploaded this photo", receipt_id=duplicate.id)
        return None

    async def _owned(self, user: User, receipt_id: uuid.UUID, *, lock: bool = False) -> Receipt:
        # Another user's receipt is indistinguishable from a missing one.
        receipt = await self.receipts.get_for_user(user.id, receipt_id, lock=lock)
        if receipt is None:
            raise NotFound("Receipt not found")
        return receipt


class _LineItemFields(Protocol):
    description: str
    quantity: Decimal
    unit_price: Decimal


def line_items(items: Iterable[_LineItemFields]) -> list[LineItem]:
    return [
        LineItem(position=i, description=li.description, quantity=li.quantity, unit_price=li.unit_price)
        for i, li in enumerate(items)
    ]


# Cursors are opaque to clients but not secret: the list query is always scoped to the signed-in user,
# so an altered cursor can only move around within that user's own receipts.
def encode_cursor(created_at: dt.datetime, receipt_id: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(f"{created_at.isoformat()}|{receipt_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[dt.datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        stamp, receipt_id = raw.split("|")
        created_at = dt.datetime.fromisoformat(stamp)
        if created_at.tzinfo is None:
            raise ValueError("naive timestamp")
        return created_at, uuid.UUID(receipt_id)
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise InvalidCursor("Invalid cursor") from exc
