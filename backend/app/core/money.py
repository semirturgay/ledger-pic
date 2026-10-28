from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def to_money(value: Decimal | float | int | None) -> Decimal:
    # str() first: Decimal(float) would carry binary noise (e.g. 0.1 -> 0.1000000000000000055...).
    return Decimal(str(value or 0)).quantize(CENT)


def line_items_total(items: Iterable[tuple[Decimal, Decimal]]) -> Decimal | None:
    """Sum of the line amounts, or None when there are no items. Like a till, each line is rounded to
    cents first (half up), so weighed items (1.670 kg x 129.95 = 217.0165 -> 217.02) match the receipt."""
    items = list(items)
    if not items:
        return None
    return sum(((quantity * unit_price).quantize(CENT, ROUND_HALF_UP) for quantity, unit_price in items), Decimal(0))


def totals_mismatch(items_total: Decimal | None, tax: Decimal | None, total: Decimal | None) -> bool:
    """True when neither items (tax-inclusive) nor items + tax (tax-exclusive) match the total."""
    if items_total is None or total is None:
        return False
    candidates = (items_total, items_total + (tax or Decimal(0)))
    return all(abs(candidate - total) > CENT for candidate in candidates)
