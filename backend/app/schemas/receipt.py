import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.money import line_items_total, totals_mismatch
from app.models import Receipt
from app.schemas.fields import STRIP, UPPER

Status = Literal["processing", "needs_review", "confirmed", "failed"]
FailureReason = Literal["not_a_receipt", "nothing_readable", "image_unreadable", "ai_unavailable"]
Category = Literal[
    "groceries", "dining", "transport", "travel", "shopping", "utilities", "entertainment", "health", "other"
]


class LineItemIn(BaseModel):
    description: Annotated[str, STRIP] = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(gt=0, max_digits=10, decimal_places=3)
    # Negative unit prices are allowed: discounts and coupons are line items too.
    unit_price: Decimal = Field(max_digits=14, decimal_places=6)  # sub-cent precision, e.g. 0.14039 per kWh


class ReceiptUpdate(BaseModel):
    """The user-corrected record. Saving it confirms the receipt."""

    merchant: Annotated[str, STRIP] = Field(min_length=1, max_length=200)
    purchased_at: dt.date
    currency: Annotated[str, UPPER] = Field(pattern=r"^[A-Z]{3}$")
    category: Category
    tax: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    total: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    line_items: list[LineItemIn] = Field(default_factory=list, max_length=200)


class LineItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    description: str
    quantity: Decimal
    unit_price: Decimal


class ReceiptListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: Status
    merchant: str | None
    purchased_at: dt.date | None
    currency: str | None
    category: Category | None
    total: Decimal | None
    created_at: dt.datetime
    extraction_attempts: int
    extraction_error: str | None
    # Set when status is `failed`: what went wrong, so the UI can offer the right next step.
    failure_reason: FailureReason | None


class ReceiptPage(BaseModel):
    """One page of the receipt list, newest first. Pass next_cursor back to get the next page; null
    means this was the last one."""

    items: list[ReceiptListItem]
    next_cursor: str | None


class ReceiptOut(ReceiptListItem):
    tax: Decimal | None
    line_items: list[LineItemOut]
    updated_at: dt.datetime
    # Server-computed sum of quantity * unit_price, and whether it disagrees with the stated total.
    prompt_version: int | None
    extraction_model: str | None
    possible_duplicate_of: uuid.UUID | None
    line_items_total: Decimal | None = None
    totals_mismatch: bool = False

    @classmethod
    def from_receipt(cls, receipt: Receipt) -> "ReceiptOut":
        items_total = line_items_total((li.quantity, li.unit_price) for li in receipt.line_items)
        return cls.model_validate(receipt).model_copy(
            update={
                "line_items_total": items_total,
                "totals_mismatch": totals_mismatch(items_total, receipt.tax, receipt.total),
            }
        )


class CurrencyTotal(BaseModel):
    currency: str
    total: Decimal
    count: int


class CategoryTotal(BaseModel):
    currency: str
    category: Category
    total: Decimal


class SummaryOut(BaseModel):
    """Rollups over confirmed receipts only. Currencies are never summed together."""

    month: str
    month_totals: list[CurrencyTotal]
    month_by_category: list[CategoryTotal]
    all_time_by_category: list[CategoryTotal]
    all_time_totals: list[CurrencyTotal]
    pending_review_count: int
