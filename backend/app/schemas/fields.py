"""Reusable field cleaners for user and model input: `Annotated[str, STRIP]`, `Annotated[Decimal, TO_CENTS]`."""

import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from pydantic import AfterValidator, BeforeValidator

from app.core.money import CENT

# Control characters (except tab and newlines) and lone surrogates: Postgres refuses NUL in text and
# jsonb, and lone surrogates can't be encoded as UTF-8, so a value containing them could never be saved.
_UNSTORABLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")


def storable(text: str) -> str:
    return _UNSTORABLE.sub("", text)


def _text(transform: Callable[[str], Any]) -> BeforeValidator:
    # Clean and strip first; non-strings pass through for Pydantic's own type check to reject.
    return BeforeValidator(lambda value: transform(storable(value).strip()) if isinstance(value, str) else value)


STRIP = _text(lambda text: text)
BLANK_TO_NONE = _text(lambda text: text or None)
UPPER = _text(str.upper)
TO_CENTS = AfterValidator(lambda value: None if value is None else value.quantize(CENT))
TO_THOUSANDTHS = AfterValidator(lambda value: value.quantize(Decimal("0.001")))
TO_MICROS = AfterValidator(lambda value: value.quantize(Decimal("0.000001")))  # unit prices: never round to cents
