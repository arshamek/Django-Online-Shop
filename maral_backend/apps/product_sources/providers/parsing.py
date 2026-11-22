"""Defensive value parsing for provider payloads. Bad values become None, never exceptions or silent guesses."""

import re
from decimal import Decimal, InvalidOperation

_TWO_PLACES = Decimal("0.01")


def clean_str(value, max_length: int | None = None) -> str:
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    text = " ".join(str(value).split())
    return text[:max_length] if max_length else text


def to_decimal(value) -> Decimal | None:
    """Finite, non-negative money amount with 2 decimal places, or None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip().replace(",", "")) if isinstance(value, str) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or number < 0:
        return None
    return number.quantize(_TWO_PLACES)


def to_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None


_GTIN_LENGTHS = {8, 12, 13, 14}


def normalize_gtin(value) -> str:
    """A GTIN-8/12/13/14 with a valid check digit, else ''. A barcode that is not a GTIN is NOT promoted."""
    digits = re.sub(r"\D", "", clean_str(value))
    if len(digits) not in _GTIN_LENGTHS or not digits.strip("0"):
        return ""
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return digits if (10 - total % 10) % 10 == check else ""


_TAGS = re.compile(r"<[^>]+>")


def strip_html(value: str) -> str:
    import html

    return " ".join(html.unescape(_TAGS.sub(" ", value or "")).split())
