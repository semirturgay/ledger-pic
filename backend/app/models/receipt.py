import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# processing -> needs_review | failed -> confirmed. failed/needs_review can go back to processing (retry).
RECEIPT_STATUSES = ("processing", "needs_review", "confirmed", "failed")
# Why a receipt is `failed`, so the UI can say what to do next.
FAILURE_REASONS = (
    "not_a_receipt",  # the model says the image isn't a receipt or invoice
    "nothing_readable",  # it is one, but no merchant, total or items could be read
    "image_unreadable",  # the file passed the upload check but couldn't be decoded
    "ai_unavailable",  # the model couldn't be used (quota, outage, configuration)
)
CATEGORIES = (
    "groceries",
    "dining",
    "transport",
    "travel",
    "shopping",
    "utilities",
    "entertainment",
    "health",
    "other",
)

# Money is NUMERIC(12, 2) end to end: Postgres numeric <-> Python Decimal <-> JSON string.
Money = Numeric(12, 2)
Quantity = Numeric(10, 3)
# Unit prices keep sub-cent precision (0.14039 USD/kWh, 1.799 EUR/l); only line amounts are rounded to cents.
UnitPrice = Numeric(14, 6)


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (
        CheckConstraint(_one_of("status", RECEIPT_STATUSES), name="ck_receipts_status"),
        CheckConstraint(_one_of("category", CATEGORIES), name="ck_receipts_category"),
        CheckConstraint(_one_of("failure_reason", FAILURE_REASONS), name="ck_receipts_failure_reason"),
        # The receipt list: newest first, cursor = (created_at, id).
        Index("ix_receipts_user_created_id", "user_id", "created_at", "id"),
        Index("ix_receipts_queue", "queued_at", postgresql_where=text("status = 'processing'")),
        # The user's latest queued job (fair queue position).
        Index("ix_receipts_user_processing", "user_id", "queued_at", postgresql_where=text("status = 'processing'")),
        # Monthly summary and the likely-duplicate lookup, both by purchase date.
        Index("ix_receipts_user_purchased", "user_id", "purchased_at"),
        # ON DELETE SET NULL on possible_duplicate_of: without it every delete scans the table.
        Index("ix_receipts_possible_duplicate_of", "possible_duplicate_of", postgresql_where=text("possible_duplicate_of IS NOT NULL")),
        # The hourly purge of rejected uploads.
        Index("ix_receipts_failed_updated", "updated_at", postgresql_where=text("status = 'failed'")),
        UniqueConstraint("user_id", "idempotency_key", name="uq_receipts_user_idempotency_key"),
        # The same file can't be imported twice by one user (exact duplicate).
        UniqueConstraint("user_id", "upload_sha256", name="uq_receipts_user_upload_sha256"),
    )
    # Fetch server-generated values (timestamps) on INSERT/UPDATE; async sessions can't lazy-load them.
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="processing")
    merchant: Mapped[str | None] = mapped_column(Text)
    purchased_at: Mapped[dt.date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(3))
    category: Mapped[str | None] = mapped_column(String(32))
    tax: Mapped[Decimal | None] = mapped_column(Money)
    total: Mapped[Decimal | None] = mapped_column(Money)
    # Every model attempt as returned, for debugging. Never read for totals.
    raw_ai_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extraction_error: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(String(32))
    prompt_version: Mapped[int | None] = mapped_column(Integer)  # which prompt drafted it
    extraction_model: Mapped[str | None] = mapped_column(String(100))  # and which model
    # An earlier receipt with the same merchant, date, total and currency (e.g. the same receipt photographed twice).
    possible_duplicate_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("receipts.id", ondelete="SET NULL"))
    # Queue bookkeeping: tries so far, when the next try is due, and which worker holds the job until when.
    extraction_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # When the job entered the queue (upload or re-extract). The queue is first come, first served by this,
    # so re-queuing an old receipt doesn't jump ahead of other people's new uploads.
    queued_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    idempotency_key: Mapped[str | None] = mapped_column(String(64))
    # SHA-256 of the uploaded file, to reject reusing an Idempotency-Key for a different image.
    upload_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    line_items: Mapped[list["LineItem"]] = relationship(
        back_populates="receipt",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="LineItem.position",
    )
    image: Mapped["ReceiptImage | None"] = relationship(cascade="all, delete-orphan", passive_deletes=True)


class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receipt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("receipts.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Quantity)
    unit_price: Mapped[Decimal] = mapped_column(UnitPrice)

    receipt: Mapped[Receipt] = relationship(back_populates="line_items")


class ReceiptImage(Base):
    """Image bytes, kept apart so listing receipts never loads them. Holds the original until the
    worker replaces it with the downscaled version (normalized=True).
    """

    __tablename__ = "receipt_images"

    receipt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("receipts.id", ondelete="CASCADE"), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(50))
    size_bytes: Mapped[int] = mapped_column(Integer)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    normalized: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


USAGE_KINDS = ("upload", "extract")


class UsageEvent(Base):
    """Append-only record of work a user asked for. Daily quotas count these, so deleting
    receipts doesn't give the allowance back. Rows older than two days are pruned."""

    __tablename__ = "usage_events"
    __table_args__ = (
        CheckConstraint(_one_of("kind", USAGE_KINDS), name="ck_usage_events_kind"),
        Index("ix_usage_events_user_kind_created", "user_id", "kind", "created_at"),
        Index("ix_usage_events_created", "created_at"),  # the global daily counts
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
