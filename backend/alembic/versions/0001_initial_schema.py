"""Initial schema: users, receipts (also the extraction queue), line_items, receipt_images, prompts

Revision ID: 0001
Revises:
Create Date: 2026-10-27
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

STATUSES = "'processing', 'needs_review', 'confirmed', 'failed'"
CATEGORIES = "'groceries', 'dining', 'transport', 'travel', 'shopping', 'utilities', 'entertainment', 'health', 'other'"
FAILURE_REASONS = "'not_a_receipt', 'nothing_readable', 'image_unreadable', 'ai_unavailable'"

# Frozen copy of prompt v1. New versions are new rows (see README), never edits to this one.
RECEIPT_EXTRACTION_V1 = """You are reading a photo of a purchase receipt or invoice. Extract it as JSON.

Rules:
- is_receipt: true only if the image shows a purchase receipt, till slip or invoice. Set it to false
  for anything else (a person, a landscape, a screenshot, a menu, a blank page, another kind of
  document), and then return null for every other field and an empty line_items list.
- Use null for any field you cannot read with confidence. Do not guess.
- merchant: the store or business name.
- date: the purchase date as YYYY-MM-DD.
- currency: ISO 4217 code such as USD, EUR, GBP, TRY. Infer it from symbols or the country only when clear.
- line_items: one entry per purchased item. quantity defaults to 1. unit_price is the price of ONE unit.
  Discounts or coupons are items with a negative unit_price.
- tax: the total tax amount printed on the receipt, or null if none is shown.
- total: the final amount paid.
- category: the best fit from: {categories}."""


def upgrade() -> None:
    now = sa.func.now()
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )
    op.create_table(
        "receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("merchant", sa.Text()),
        sa.Column("purchased_at", sa.Date()),
        sa.Column("currency", sa.String(3)),
        sa.Column("category", sa.String(32)),
        sa.Column("tax", sa.Numeric(12, 2)),
        sa.Column("total", sa.Numeric(12, 2)),
        sa.Column("raw_ai_json", postgresql.JSONB()),
        sa.Column("extraction_error", sa.Text()),
        sa.Column("failure_reason", sa.String(32)),
        sa.Column("prompt_version", sa.Integer()),
        # Extraction queue: a `processing` receipt is a job; workers lease it (app/workers/).
        sa.Column("extraction_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.Uuid()),
        # Upload idempotency: the client's key and the file hash, to reject a key reused for another file.
        sa.Column("idempotency_key", sa.String(64)),
        sa.Column("upload_sha256", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_receipts_status"),
        sa.CheckConstraint(f"category IN ({CATEGORIES})", name="ck_receipts_category"),
        sa.CheckConstraint(f"failure_reason IN ({FAILURE_REASONS})", name="ck_receipts_failure_reason"),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_receipts_user_idempotency_key"),
    )
    op.create_index("ix_receipts_user_created", "receipts", ["user_id", "created_at"])
    op.create_index("ix_receipts_queue", "receipts", ["created_at"], postgresql_where=sa.text("status = 'processing'"))
    op.create_table(
        "line_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("receipt_id", sa.Uuid(), sa.ForeignKey("receipts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
    )
    op.create_index("ix_line_items_receipt_id", "line_items", ["receipt_id"])
    op.create_table(
        "receipt_images",
        sa.Column("receipt_id", sa.Uuid(), sa.ForeignKey("receipts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("content_type", sa.String(50), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("normalized", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    prompts = op.create_table(
        "prompts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_prompts_name_version"),
    )
    op.create_index("uq_prompts_one_active", "prompts", ["name"], unique=True, postgresql_where=sa.text("is_active"))
    op.bulk_insert(
        prompts, [{"name": "receipt_extraction", "version": 1, "template": RECEIPT_EXTRACTION_V1, "is_active": True}]
    )


def downgrade() -> None:
    for table in ("prompts", "receipt_images", "line_items", "receipts", "users"):
        op.drop_table(table)
