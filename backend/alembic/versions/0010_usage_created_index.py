"""Index usage_events by time alone, for the global and new-account daily counts

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-28
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_usage_events_created", "usage_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_events_created", table_name="usage_events")
