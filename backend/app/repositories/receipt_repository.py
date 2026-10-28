import datetime as dt
import uuid
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, Delete, delete, func, or_, select, text, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Receipt, ReceiptImage, UsageEvent, User

UNCONFIRMED_STATUSES = ("processing", "needs_review", "failed")
# Postgres NOTIFY channel that wakes idle workers; a constant, never built from input.
JOBS_CHANNEL = "receipt_jobs"
# Spacing between one user's queued jobs in queue order (see fair_queue_slot): about one job's time.
FAIR_QUEUE_SPACING = dt.timedelta(seconds=30)
ABANDONED_JOB = "Processing this image kept failing. Try again, or fill it in yourself."


class ReceiptRepository:
    """All receipt queries. User-facing ones filter on the owner in SQL; none commit.

    Async sessions can't lazy-load, so queries whose line items are read use selectinload.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db


    def add(self, receipt: Receipt) -> None:
        self.db.add(receipt)

    async def get_for_user(self, user_id: uuid.UUID, receipt_id: uuid.UUID, *, lock: bool = False) -> Receipt | None:
        query = (
            select(Receipt)
            .where(Receipt.id == receipt_id, Receipt.user_id == user_id)
            .options(selectinload(Receipt.line_items))
            # Always re-read: a row already in this session may be stale after a commit.
            .execution_options(populate_existing=True)
        )
        if lock:
            # Serializes status transitions against the extraction worker writing the same row.
            query = query.with_for_update()
        return await self.db.scalar(query)

    async def get_by_idempotency_key(self, user_id: uuid.UUID, key: str) -> Receipt | None:
        query = (
            select(Receipt)
            .where(Receipt.user_id == user_id, Receipt.idempotency_key == key)
            .options(selectinload(Receipt.line_items))
        )
        return await self.db.scalar(query)

    async def get_by_sha256(self, user_id: uuid.UUID, sha256: str) -> Receipt | None:
        return await self.db.scalar(select(Receipt).where(Receipt.user_id == user_id, Receipt.upload_sha256 == sha256))

    def record_usage(self, user_id: uuid.UUID, kind: str) -> None:
        self.db.add(UsageEvent(user_id=user_id, kind=kind))

    async def lock_quota(self, user_id: uuid.UUID | None) -> None:
        """Serialize quota checks until commit, so simultaneous requests can't all pass: one user's
        (user_id), or the global ceiling (None). Always taken in that order, so they can't deadlock."""
        key = f"quota:{user_id}" if user_id else "quota:global"
        # Waiting is capped: a pile-up of uploads fails fast instead of holding every pooled connection.
        await self.db.execute(text("SET LOCAL lock_timeout = '5s'"))
        await self.db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})

    async def count_new_account_usage_since(self, since: dt.datetime, created_after: dt.datetime) -> int:
        query = (
            select(func.count())
            .select_from(UsageEvent)
            .join(User, User.id == UsageEvent.user_id)
            .where(UsageEvent.created_at >= since, User.created_at >= created_after)
        )
        return await self.db.scalar(query) or 0

    async def count_all_usage_since(self, since: dt.datetime) -> int:
        query = select(func.count()).select_from(UsageEvent).where(UsageEvent.created_at >= since)
        return await self.db.scalar(query) or 0

    async def count_user_usage_since(self, user_id: uuid.UUID, since: dt.datetime) -> int:
        """Every kind (uploads and re-runs) for this user since `since`."""
        query = select(func.count()).select_from(UsageEvent).where(UsageEvent.user_id == user_id, UsageEvent.created_at >= since)
        return await self.db.scalar(query) or 0

    async def count_usage_since(self, user_id: uuid.UUID, kind: str, since: dt.datetime) -> int:
        """From the append-only usage log, so deleted receipts still count toward today's quota."""
        query = (
            select(func.count())
            .select_from(UsageEvent)
            .where(UsageEvent.user_id == user_id, UsageEvent.kind == kind, UsageEvent.created_at >= since)
        )
        return await self.db.scalar(query) or 0

    async def prune_usage(self, before: dt.datetime) -> int:
        return await self._delete(delete(UsageEvent).where(UsageEvent.created_at < before))

    async def stored_image_bytes(self, user_id: uuid.UUID) -> int:
        query = (
            select(func.coalesce(func.sum(ReceiptImage.size_bytes), 0))
            .join(Receipt, Receipt.id == ReceiptImage.receipt_id)
            .where(Receipt.user_id == user_id)
        )
        return await self.db.scalar(query) or 0

    async def find_likely_duplicate(self, receipt: Receipt) -> uuid.UUID | None:
        """An earlier receipt of the same user with the same merchant, date, total and currency."""
        merchant, purchased_at, total, currency = receipt.merchant, receipt.purchased_at, receipt.total, receipt.currency
        if merchant is None or purchased_at is None or total is None or currency is None:
            return None
        query = (
            select(Receipt.id)
            .where(
                Receipt.user_id == receipt.user_id,
                Receipt.id != receipt.id,
                func.lower(Receipt.merchant) == merchant.lower(),
                Receipt.purchased_at == purchased_at,
                Receipt.total == total,
                Receipt.currency == currency,
                Receipt.created_at < receipt.created_at,
            )
            .order_by(Receipt.created_at)
            .limit(1)
        )
        return await self.db.scalar(query)

    async def purge_rejected(self, before: dt.datetime) -> int:
        """Delete uploads that turned out not to be receipts (or were undecodable) and are older than `before`."""
        return await self._delete(
            delete(Receipt).where(
                Receipt.status == "failed",
                Receipt.failure_reason.in_(("not_a_receipt", "image_unreadable")),
                Receipt.updated_at < before,
            )
        )

    async def list_for_user(
        self, user_id: uuid.UUID, limit: int, after: tuple[dt.datetime, uuid.UUID] | None = None
    ) -> list[Receipt]:
        """Newest first. `after` is the (created_at, id) of the last receipt already shown: keyset
        pagination, so pages stay stable while new uploads arrive and every page costs the same."""
        query = select(Receipt).where(Receipt.user_id == user_id)
        if after is not None:
            query = query.where(tuple_(Receipt.created_at, Receipt.id) < tuple_(*after))
        query = (
            query.order_by(Receipt.created_at.desc(), Receipt.id.desc())
            .limit(limit)
        )
        return list(await self.db.scalars(query))

    async def get_image_for_user(self, user_id: uuid.UUID, receipt_id: uuid.UUID) -> ReceiptImage | None:
        query = (
            select(ReceiptImage)
            .join(Receipt, Receipt.id == ReceiptImage.receipt_id)
            .where(Receipt.id == receipt_id, Receipt.user_id == user_id)
        )
        return await self.db.scalar(query)

    async def delete(self, receipt: Receipt) -> None:
        # Line items and the image row go with it via ON DELETE CASCADE.
        await self.db.delete(receipt)

    async def confirmed_totals_by_currency(
        self, user_id: uuid.UUID, start: dt.date | None = None, end: dt.date | None = None
    ) -> list[tuple[str, Decimal, int]]:
        query = (
            select(Receipt.currency, func.sum(Receipt.total), func.count())
            .where(*self._confirmed(user_id, start, end))
            .group_by(Receipt.currency)
            .order_by(Receipt.currency)
        )
        # Confirmed receipts always have a currency and a total (ReceiptUpdate requires both).
        return cast(list[tuple[str, Decimal, int]], list(await self.db.execute(query)))

    async def confirmed_totals_by_category(
        self, user_id: uuid.UUID, start: dt.date | None = None, end: dt.date | None = None
    ) -> list[tuple[str, str, Decimal]]:
        total = func.sum(Receipt.total)
        query = (
            select(Receipt.currency, Receipt.category, total)
            .where(*self._confirmed(user_id, start, end))
            .group_by(Receipt.currency, Receipt.category)
            .order_by(Receipt.currency, total.desc())
        )
        return cast(list[tuple[str, str, Decimal]], list(await self.db.execute(query)))

    async def count_unconfirmed(self, user_id: uuid.UUID) -> int:
        query = (
            select(func.count())
            .select_from(Receipt)
            .where(Receipt.user_id == user_id, Receipt.status.in_(UNCONFIRMED_STATUSES))
        )
        return await self.db.scalar(query) or 0


    async def notify_job_queued(self) -> None:
        """Wake idle workers. Postgres delivers NOTIFY on commit, so they never see an uncommitted job.
        Best effort (fire-and-forget): a worker that isn't listening misses it and finds the job by polling."""
        await self.db.execute(text(f"NOTIFY {JOBS_CHANNEL}"))

    async def fair_queue_slot(self, user_id: uuid.UUID) -> dt.datetime:
        """Where a new job of this user goes in the queue (jobs are claimed by queued_at): now, or just
        after the user's own latest queued job, whichever is later. A batch from one account is spread
        out, so other people's uploads are interleaved with it instead of waiting for all of it. Only
        the order changes: every job is claimable right away. Called under the user's quota lock."""
        now = (await self.db.execute(select(func.now()))).scalar_one()
        latest = await self.db.scalar(
            select(func.max(Receipt.queued_at)).where(Receipt.user_id == user_id, Receipt.status == "processing")
        )
        return max(now, latest + FAIR_QUEUE_SPACING) if latest is not None else now

    async def claim_next_job(
        self, now: dt.datetime, lease: dt.timedelta, max_attempts: int
    ) -> tuple[uuid.UUID, uuid.UUID] | None:
        """Lease the longest-queued due job, or None. SKIP LOCKED keeps concurrent workers apart; the row
        lock ends at the caller's commit, after which the lease (token + expiry) protects the job.
        """
        # A job whose last allowed attempt never recorded an outcome (worker crashed or was killed
        # mid-job) is failed here, so one poison upload can't be retried forever.
        await self.db.execute(
            update(Receipt)
            .where(
                Receipt.status == "processing",
                Receipt.extraction_attempts >= max_attempts,
                Receipt.lease_expires_at.is_not(None),
                Receipt.lease_expires_at <= now,
            )
            .values(
                status="failed",
                failure_reason="ai_unavailable",
                extraction_error=ABANDONED_JOB,
                lease_token=None,
                lease_expires_at=None,
                next_attempt_at=None,
            )
        )
        due = (
            select(Receipt.id)
            .where(
                Receipt.status == "processing",
                or_(Receipt.next_attempt_at.is_(None), Receipt.next_attempt_at <= now),
                or_(Receipt.lease_expires_at.is_(None), Receipt.lease_expires_at <= now),
            )
            .order_by(Receipt.queued_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        receipt_id = await self.db.scalar(due)
        if receipt_id is None:
            return None
        token = uuid.uuid4()
        await self.db.execute(
            update(Receipt)
            .where(Receipt.id == receipt_id)
            .values(
                lease_token=token,
                lease_expires_at=now + lease,
                extraction_attempts=Receipt.extraction_attempts + 1,
            )
        )
        return receipt_id, token

    async def get_image(self, receipt_id: uuid.UUID) -> ReceiptImage | None:
        return await self.db.get(ReceiptImage, receipt_id)

    async def get_leased(self, receipt_id: uuid.UUID, token: uuid.UUID) -> Receipt | None:
        """The job's receipt, only if this lease still owns it (not deleted, not reclaimed)."""
        query = (
            select(Receipt)
            .where(Receipt.id == receipt_id, Receipt.lease_token == token, Receipt.status == "processing")
            .options(selectinload(Receipt.line_items))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return await self.db.scalar(query)


    @staticmethod
    def _confirmed(user_id: uuid.UUID, start: dt.date | None, end: dt.date | None) -> list[ColumnElement[bool]]:
        conditions = [Receipt.user_id == user_id, Receipt.status == "confirmed"]
        if start is not None:
            conditions.append(Receipt.purchased_at >= start)
        if end is not None:
            conditions.append(Receipt.purchased_at < end)
        return conditions

    async def _delete(self, statement: Delete) -> int:
        """Run a DELETE and return how many rows it removed."""
        result = cast(CursorResult[Any], await self.db.execute(statement))
        return result.rowcount or 0
