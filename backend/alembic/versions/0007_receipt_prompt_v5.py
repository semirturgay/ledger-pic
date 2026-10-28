"""Receipt extraction prompt v5 = v4 + keep unit-price decimals + tax lines are never items; becomes active

From a PG&E bill: v4 rounded "@ $0.14039" to 0.14 and also listed the "Utility Users' Tax" line as an item.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-27
"""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

REWRITES = [
    (
        "- unit_price: the price of ONE unit, never the line total. If only a line total is printed, use quantity 1 "
        "and that total as unit_price.\n",
        "- unit_price: the price of ONE unit, never the line total. If only a line total is printed, use quantity 1 "
        "and that total as unit_price.\n"
        "- Copy unit_price with every decimal printed (for example 0.14039 or 4.59959); never round it. "
        "Only line amounts are in cents.\n",
    ),
    (
        "- Do not list subtotals, tax lines, totals, payment lines, change given, or card details as items.\n",
        "- Do not list subtotals, totals, payment lines, change given, or card details as items.\n"
        "- Tax lines (VAT, KDV, sales tax, utility users' tax) are never items: their amount goes in tax only.\n",
    ),
]


def upgrade() -> None:
    template = op.get_bind().execute(
        sa.text("SELECT template FROM prompts WHERE type = 'receipt_extraction' AND version = 4")
    ).scalar_one()
    for old, new in REWRITES:
        if template.count(old) != 1:
            raise RuntimeError("prompt v4 is not the expected text; write v5 by hand instead")
        template = template.replace(old, new)
    op.execute("UPDATE prompts SET is_active = false WHERE type = 'receipt_extraction'")
    op.execute(
        sa.text("INSERT INTO prompts (type, version, template, is_active) VALUES ('receipt_extraction', 5, :t, true)")
        .bindparams(t=template)
    )


def downgrade() -> None:
    op.execute("DELETE FROM prompts WHERE type = 'receipt_extraction' AND version = 5")
    op.execute("UPDATE prompts SET is_active = true WHERE type = 'receipt_extraction' AND version = 4")
