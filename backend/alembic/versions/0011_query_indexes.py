"""Indexes for every query path: cursor pagination, summary, purge, fair queue, duplicate FK

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-28
"""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_receipts_user_created_id", "receipts", ["user_id", "created_at", "id"])
    op.drop_index("ix_receipts_user_created", table_name="receipts")
    op.create_index(
        "ix_receipts_user_processing", "receipts", ["user_id", "queued_at"], postgresql_where=sa.text("status = 'processing'")
    )
    op.create_index("ix_receipts_user_purchased", "receipts", ["user_id", "purchased_at"])
    op.create_index(
        "ix_receipts_possible_duplicate_of",
        "receipts",
        ["possible_duplicate_of"],
        postgresql_where=sa.text("possible_duplicate_of IS NOT NULL"),
    )
    op.create_index("ix_receipts_failed_updated", "receipts", ["updated_at"], postgresql_where=sa.text("status = 'failed'"))


def downgrade() -> None:
    op.drop_index("ix_receipts_failed_updated", table_name="receipts")
    op.drop_index("ix_receipts_possible_duplicate_of", table_name="receipts")
    op.drop_index("ix_receipts_user_purchased", table_name="receipts")
    op.drop_index("ix_receipts_user_processing", table_name="receipts")
    op.create_index("ix_receipts_user_created", "receipts", ["user_id", "created_at"])
    op.drop_index("ix_receipts_user_created_id", table_name="receipts")
