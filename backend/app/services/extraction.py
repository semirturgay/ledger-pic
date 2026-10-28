"""Receipt image -> structured record: schema-constrained JSON from a vision model (app.services.llm),
Pydantic validation, one repair attempt with the errors, then a partial salvage. Only an unusable model
raises (ExtractionFailed). Provider-independent: no model SDK is imported here."""

import asyncio
import datetime as dt
import json
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import Settings
from app.models import CATEGORIES
from app.schemas.fields import BLANK_TO_NONE, STRIP, TO_CENTS, TO_MICROS, TO_THOUSANDTHS, UPPER, storable
from app.schemas.receipt import Category
from app.services.llm import VisionModel, vision_model_from_settings

log = logging.getLogger(__name__)


RETRY_SUFFIX = """

Your previous answer did not pass validation:
{errors}
Return corrected JSON that follows the rules above."""

_nullable_string = {"type": ["string", "null"]}
_nullable_number = {"type": ["number", "null"]}

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_receipt": {"type": "boolean", "description": "Is this a purchase receipt or invoice?"},
        "merchant": _nullable_string,
        "date": {**_nullable_string, "description": "Purchase date, YYYY-MM-DD"},
        "currency": {**_nullable_string, "description": "ISO 4217 code"},
        "category": {"type": ["string", "null"], "enum": [*CATEGORIES, None]},
        "line_items": {"type": "array", "items": {
            "type": "object",
            "properties": {"description": {"type": "string"}, "quantity": {"type": "number"}, "unit_price": {"type": "number"}},
            "required": ["description", "quantity", "unit_price"],
        }},
        "tax": _nullable_number,
        "total": _nullable_number,
    },
    "required": ["is_receipt", "merchant", "date", "currency", "category", "line_items", "tax", "total"],
}


class ExtractedLineItem(BaseModel):
    description: Annotated[str, STRIP] = Field(min_length=1, max_length=500)
    # Bounds just inside the columns (Numeric(10,3), Numeric(14,6)) so rounding can't push a value past them.
    quantity: Annotated[Decimal, TO_THOUSANDTHS] = Field(default=Decimal(1), gt=0, lt=Decimal("9999999.9995"))
    unit_price: Annotated[Decimal, TO_MICROS] = Field(gt=Decimal("-99999999.9999995"), lt=Decimal("99999999.9999995"))


class ExtractedReceipt(BaseModel):
    """What we accept from the model. Every field may be null: the human fills the gaps."""

    model_config = ConfigDict(populate_by_name=True)

    # Defaults to True so a partial answer that lost this field is still treated as a receipt.
    is_receipt: bool = True
    merchant: Annotated[str | None, BLANK_TO_NONE] = Field(default=None, max_length=200)
    purchased_at: dt.date | None = Field(default=None, alias="date")
    currency: Annotated[str | None, UPPER] = Field(default=None, pattern=r"^[A-Z]{3}$")
    category: Category | None = None
    line_items: list[ExtractedLineItem] = Field(default_factory=list, max_length=200)
    tax: Annotated[Decimal | None, TO_CENTS] = Field(default=None, ge=0, lt=Decimal("1000000000"))
    total: Annotated[Decimal | None, TO_CENTS] = Field(default=None, ge=0, lt=Decimal("1000000000"))

    @property
    def is_empty(self) -> bool:
        """Nothing worth reviewing: no merchant, no total and no line items."""
        return self.merchant is None and self.total is None and not self.line_items


SCALAR_FIELDS = ("is_receipt", "merchant", "date", "currency", "category", "tax", "total")


class NoLongerWanted(Exception):
    """The receipt was deleted before the model was called."""


StillWanted = Callable[[], Awaitable[bool]]


@dataclass
class ExtractionResult:
    """raw: every attempt's output and validation errors (stored as raw_ai_json); error: why it's partial, if it is."""

    data: ExtractedReceipt
    raw: dict[str, Any]
    error: str | None


class ReceiptExtractor:
    """Turns a receipt image into a validated draft, using whichever VisionModel it is given."""

    def __init__(self, vision: VisionModel, max_concurrency: int) -> None:
        self._vision = vision
        # Bounds concurrent model calls per process; with N processes the ceiling is N x this.
        self._slots = asyncio.Semaphore(max_concurrency)

    @property
    def model(self) -> str:
        """Recorded on each receipt next to the prompt version, so every draft can be traced."""
        return self._vision.name

    @classmethod
    def from_settings(cls, settings: Settings) -> "ReceiptExtractor":
        return cls(vision_model_from_settings(settings), settings.ai_max_concurrency)

    async def extract(
        self, image: bytes, mime_type: str, base_prompt: str, still_wanted: StillWanted | None = None
    ) -> ExtractionResult:
        """Ask the model for the receipt. If the answer doesn't fit the schema, ask once more with the
        exact validation errors; if that still doesn't fit, keep whatever fields are valid (salvage).

        `still_wanted` is asked right before each model call, once a model slot is free: a receipt
        deleted while waiting for one is never sent to the provider."""
        text = await self._ask(image, mime_type, base_prompt, still_wanted)
        data, errors = _validate(text)
        attempts = [{"output": _as_json(text), "validation_errors": errors}]
        if data is not None:
            return ExtractionResult(data=data, raw={"attempts": attempts}, error=None)

        log.info("Extraction output failed validation: %s", errors)
        repair_prompt = base_prompt + RETRY_SUFFIX.format(errors="\n".join(f"- {e}" for e in errors))
        text = await self._ask(image, mime_type, repair_prompt, still_wanted)
        data, errors = _validate(text)
        attempts.append({"output": _as_json(text), "validation_errors": errors})
        if data is not None:
            return ExtractionResult(data=data, raw={"attempts": attempts}, error=None)

        log.info("Extraction output failed validation again: %s", errors)
        return ExtractionResult(
            data=salvage(attempts[-1]["output"]),
            raw={"attempts": attempts},
            error="The AI output was incomplete or malformed. Fields it could not read are empty; please fill them in.",
        )

    async def _ask(self, image: bytes, mime_type: str, prompt: str, still_wanted: StillWanted | None) -> str:
        """One model call under a model slot, held only for the call itself."""
        async with self._slots:
            if still_wanted is not None and not await still_wanted():
                raise NoLongerWanted()
            return await self._vision.read(image, mime_type, prompt, RESPONSE_SCHEMA)


def salvage(output: Any) -> ExtractedReceipt:
    """Keep every field and line item that validates on its own; drop the rest."""
    if not isinstance(output, dict):
        return ExtractedReceipt()

    kept: dict[str, Any] = {}
    for name in SCALAR_FIELDS:
        if name not in output:
            continue
        try:
            ExtractedReceipt.model_validate({name: output[name]})
        except ValidationError:
            continue
        kept[name] = output[name]

    items = output.get("line_items")
    if isinstance(items, list):
        valid_items = []
        for item in items:
            try:
                valid_items.append(ExtractedLineItem.model_validate(item))
            except ValidationError:
                continue
        kept["line_items"] = valid_items[:200]

    return ExtractedReceipt.model_validate(kept)


# The largest valid answer (200 line items of 500 characters) is about 110 KB; anything past this is
# not a receipt and is not kept in raw_ai_json.
MAX_RAW_OUTPUT_CHARS = 200_000


def _validate(text: str) -> tuple[ExtractedReceipt | None, list[str]]:
    """The parsed receipt, or None and what was wrong with the output (fed back to the model)."""
    try:
        return ExtractedReceipt.model_validate_json(text), []
    except ValidationError as exc:
        return None, _describe(exc)
    except RecursionError:
        return None, ["output is nested too deeply"]


def _as_json(text: str) -> Any:
    """Parse model text into JSON if at all possible (models sometimes wrap it in prose or fences),
    with every string made storable: one NUL in the answer must not make the whole outcome unsavable."""
    try:
        return _storable(_parse(text))
    except RecursionError:  # parsed, but nested too deep to walk: keep the text instead
        return {"unparsed_text": storable(text[:5000])}


def _storable(value: Any) -> Any:
    if isinstance(value, str):
        return storable(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None  # json.loads accepts NaN/Infinity; jsonb doesn't
    if isinstance(value, list):
        return [_storable(item) for item in value]
    if isinstance(value, dict):
        return {storable(str(key)): _storable(item) for key, item in value.items()}
    return value


def _parse(text: str) -> Any:
    if len(text) > MAX_RAW_OUTPUT_CHARS:
        return {"unparsed_text": text[:5000], "truncated": True}
    try:
        return json.loads(text)
    except (ValueError, RecursionError):  # JSONDecodeError is a ValueError; deep nesting recurses
        pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(text[start : end + 1])
        except (ValueError, RecursionError):
            pass
    return {"unparsed_text": text[:5000]}


def _describe(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in err['loc']) or 'response'}: {err['msg']}" for err in exc.errors()[:20]]
