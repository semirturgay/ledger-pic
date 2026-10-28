"""Receipt extraction prompt v3 = v2 + line-item rules learned from a real Turkish (Migros) receipt; becomes active

v2 misread "%20" (the VAT-rate column) as a quantity, and attached "0,758 KG x 1.349,95 TL/KG" lines to the
item above instead of below, then recomputed those items instead of using their printed amounts.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

ANCHOR = '- unit_price: the price of ONE unit, never the line total. If only a line total is printed, use quantity 1 and that total as unit_price.\n'
NEW_RULES = (
    "- The amount printed on an item's line is authoritative: quantity x unit_price must equal it. "
    "If they don't match, use quantity 1 and the printed amount as unit_price.\n"
    "- A percentage printed next to an item (for example %1, %10, %20) is its VAT rate, never its quantity.\n"
    '- A weight or multiplier printed on its own line (for example "1,670 KG x 129,95 TL/KG") belongs to the item '
    "printed directly below it, not the one above.\n"
)


def upgrade() -> None:
    v2 = op.get_bind().execute(
        sa.text("SELECT template FROM prompts WHERE type = 'receipt_extraction' AND version = 2")
    ).scalar_one()
    if v2.count(ANCHOR) != 1:
        raise RuntimeError("prompt v2 is not the expected text; write v3 by hand instead")
    v3 = v2.replace(ANCHOR, ANCHOR + NEW_RULES)
    op.execute("UPDATE prompts SET is_active = false WHERE type = 'receipt_extraction'")
    op.execute(
        sa.text("INSERT INTO prompts (type, version, template, is_active) VALUES ('receipt_extraction', 3, :t, true)")
        .bindparams(t=v3)
    )


def downgrade() -> None:
    op.execute("DELETE FROM prompts WHERE type = 'receipt_extraction' AND version = 3")
    op.execute("UPDATE prompts SET is_active = true WHERE type = 'receipt_extraction' AND version = 2")
