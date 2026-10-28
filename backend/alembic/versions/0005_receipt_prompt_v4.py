"""Receipt extraction prompt v4 = v3, but weighed items keep their weight and price per unit; becomes active

v3 fixed the totals but recorded weighed goods as 1 x line amount (e.g. 1 x 1023.26 instead of
0.758 kg x 1349.95). v4 uses the separate weight line for the item below it.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-27
"""

import sqlalchemy as sa

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

REWRITES = [
    (
        "- The amount printed on an item's line is authoritative: quantity x unit_price must equal it. "
        "If they don't match, use quantity 1 and the printed amount as unit_price.\n",
        "- The amount printed on an item's line is authoritative: quantity x unit_price, rounded to cents, must "
        "equal it. Only when no weight or count explains the amount, use quantity 1 and the printed amount.\n",
    ),
    (
        '- A weight or multiplier printed on its own line (for example "1,670 KG x 129,95 TL/KG") belongs to the item '
        "printed directly below it, not the one above.\n",
        '- A weight or multiplier printed on its own line (for example "1,670 KG x 129,95 TL/KG") belongs to the item '
        "printed directly below it, not the one above. Use it for that item: quantity is the weight or count "
        "(1.670) and unit_price is the price per unit (129.95), so quantity x unit_price rounds to the printed "
        "amount (217.02).\n",
    ),
]


def upgrade() -> None:
    template = op.get_bind().execute(
        sa.text("SELECT template FROM prompts WHERE type = 'receipt_extraction' AND version = 3")
    ).scalar_one()
    for old, new in REWRITES:
        if template.count(old) != 1:
            raise RuntimeError("prompt v3 is not the expected text; write v4 by hand instead")
        template = template.replace(old, new)
    op.execute("UPDATE prompts SET is_active = false WHERE type = 'receipt_extraction'")
    op.execute(
        sa.text("INSERT INTO prompts (type, version, template, is_active) VALUES ('receipt_extraction', 4, :t, true)")
        .bindparams(t=template)
    )


def downgrade() -> None:
    op.execute("DELETE FROM prompts WHERE type = 'receipt_extraction' AND version = 4")
    op.execute("UPDATE prompts SET is_active = true WHERE type = 'receipt_extraction' AND version = 3")
