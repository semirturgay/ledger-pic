"""Prompts are keyed by an explicit, constrained `type` instead of a free-form `name`

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TYPES = "'receipt_extraction'"


def upgrade() -> None:
    op.alter_column("prompts", "name", new_column_name="type")
    # The unique constraint's name mentioned `name`; its column follows the rename automatically.
    op.execute("ALTER TABLE prompts RENAME CONSTRAINT uq_prompts_name_version TO uq_prompts_type_version")
    op.create_check_constraint("ck_prompts_type", "prompts", f"type IN ({TYPES})")


def downgrade() -> None:
    op.drop_constraint("ck_prompts_type", "prompts", type_="check")
    op.execute("ALTER TABLE prompts RENAME CONSTRAINT uq_prompts_type_version TO uq_prompts_name_version")
    op.alter_column("prompts", "type", new_column_name="name")
