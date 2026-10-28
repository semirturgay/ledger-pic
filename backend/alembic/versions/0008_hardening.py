"""Hardening: immutable prompts, duplicate detection, and which model drafted each receipt

- A trigger rejects editing a prompt's type, version, text or creation time, and deleting a version
  that receipts were drafted with. Only `is_active` may change: new wording means a new version.
- (user_id, upload_sha256) is unique: the same file can't be imported twice by the same user.
- possible_duplicate_of points at an earlier receipt with the same merchant, date, total and currency
  (a re-photographed receipt has a different file hash, so the exact check can't catch it).
- extraction_model records the model that produced the draft, next to prompt_version.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-27
"""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


# Also loaded by tests/conftest.py, which builds the schema with create_all instead of migrations.
IMMUTABLE_PROMPTS_FUNCTION = """
    CREATE OR REPLACE FUNCTION prompts_are_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF EXISTS (SELECT 1 FROM receipts WHERE prompt_version = OLD.version) THEN
                RAISE EXCEPTION 'prompt % v% drafted existing receipts and cannot be deleted', OLD.type, OLD.version;
            END IF;
            RETURN OLD;
        END IF;
        IF (NEW.type, NEW.version, NEW.template, NEW.created_at) IS DISTINCT FROM
           (OLD.type, OLD.version, OLD.template, OLD.created_at) THEN
            RAISE EXCEPTION 'prompts are immutable: add a new version instead of editing % v%', OLD.type, OLD.version;
        END IF;
        RETURN NEW;
    END $$;
"""
IMMUTABLE_PROMPTS_TRIGGER = """
    CREATE TRIGGER prompts_immutable BEFORE UPDATE OR DELETE ON prompts
    FOR EACH ROW EXECUTE FUNCTION prompts_are_immutable();
"""


def upgrade() -> None:
    op.execute(IMMUTABLE_PROMPTS_FUNCTION)
    op.execute(IMMUTABLE_PROMPTS_TRIGGER)
    op.create_unique_constraint("uq_receipts_user_upload_sha256", "receipts", ["user_id", "upload_sha256"])
    op.add_column(
        "receipts", sa.Column("possible_duplicate_of", sa.Uuid(), sa.ForeignKey("receipts.id", ondelete="SET NULL"))
    )
    op.add_column("receipts", sa.Column("extraction_model", sa.String(100)))


def downgrade() -> None:
    op.drop_column("receipts", "extraction_model")
    op.drop_column("receipts", "possible_duplicate_of")
    op.drop_constraint("uq_receipts_user_upload_sha256", "receipts", type_="unique")
    op.execute("DROP TRIGGER prompts_immutable ON prompts")
    op.execute("DROP FUNCTION prompts_are_immutable()")
