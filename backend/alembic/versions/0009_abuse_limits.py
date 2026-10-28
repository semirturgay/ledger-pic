"""Abuse limits: queue order by enqueue time, and an append-only usage log for daily quotas

- receipts.queued_at: set on upload and on every re-extract; the queue serves the oldest queued_at first,
  so re-queuing an old receipt can't jump ahead of newer uploads. Backfilled from created_at.
- usage_events: one row per upload and per re-extract. Daily quotas count these instead of live receipts,
  so deleting receipts doesn't reset the allowance.

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-27
"""

import sqlalchemy as sa

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "receipts", sa.Column("queued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
    )
    op.execute("UPDATE receipts SET queued_at = created_at")
    op.drop_index("ix_receipts_queue", table_name="receipts")
    op.create_index("ix_receipts_queue", "receipts", ["queued_at"], postgresql_where=sa.text("status = 'processing'"))
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('upload', 'extract')", name="ck_usage_events_kind"),
    )
    op.create_index("ix_usage_events_user_kind_created", "usage_events", ["user_id", "kind", "created_at"])
    # Seed today's uploads so the new daily count starts from what users already did.
    op.execute(
        "INSERT INTO usage_events (user_id, kind, created_at) "
        "SELECT user_id, 'upload', created_at FROM receipts WHERE created_at > now() - interval '1 day'"
    )


def downgrade() -> None:
    op.drop_index("ix_usage_events_user_kind_created", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_index("ix_receipts_queue", table_name="receipts")
    op.create_index("ix_receipts_queue", "receipts", ["created_at"], postgresql_where=sa.text("status = 'processing'"))
    op.drop_column("receipts", "queued_at")
