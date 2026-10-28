"""Unit prices keep six decimals: NUMERIC(12,2) -> NUMERIC(14,6)

Rounding a unit price to cents before multiplying loses up to half a cent per unit: on a
17,384.64 kWh utility line, 0.14039 -> 0.14 was $6.78 off. Line amounts stay in cents.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-27
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("line_items", "unit_price", type_=sa.Numeric(14, 6), existing_type=sa.Numeric(12, 2))


def downgrade() -> None:
    # Rounds any sub-cent unit prices back to cents.
    op.alter_column("line_items", "unit_price", type_=sa.Numeric(12, 2), existing_type=sa.Numeric(14, 6))
