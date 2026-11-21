"""Per-request content language + display currency (API_CONVENTIONS sections 3 and 4)."""

import re
from dataclasses import dataclass
from decimal import Decimal

from apps.common.exceptions import InvalidFilter
from apps.common.languages import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, language_chain

DISPLAY_CURRENCIES = ("IRT", "IRR")
DEFAULT_CURRENCY = "IRT"
_ACCEPT_ITEM = re.compile(r"^\s*([A-Za-z]{1,8})(?:-[A-Za-z0-9-]+)?\s*(?:;\s*q=([0-9.]+))?\s*$")


@dataclass(frozen=True)
class CatalogContext:
    request: object
    lang: str
    chain: tuple
    currency: str

    @property
    def cache_parts(self) -> dict:
        return {"lang": self.lang, "currency": self.currency}


def _from_accept_language(header: str) -> str:
    best, best_q = DEFAULT_LANGUAGE, -1.0
    for item in header.split(",")[:10]:
        match = _ACCEPT_ITEM.match(item)
        if not match:
            continue
        code, q = match.group(1).lower(), float(match.group(2) or 1)
        if code in SUPPORTED_LANGUAGES and q > best_q:
            best, best_q = code, q
    return best


def get_context(request) -> CatalogContext:
    params = request.query_params
    requested = (params.get("lang") or "").lower()
    lang = (
        requested
        if requested in SUPPORTED_LANGUAGES
        else _from_accept_language(request.headers.get("Accept-Language", ""))
    )

    currency = (params.get("currency") or "").upper()
    if currency and currency not in DISPLAY_CURRENCIES:
        raise InvalidFilter(fields={"currency": [f"Use one of: {', '.join(DISPLAY_CURRENCIES)}."]})
    if not currency:
        header = (request.headers.get("Accept-Currency") or "").upper().strip()
        currency = header if header in DISPLAY_CURRENCIES else DEFAULT_CURRENCY
    return CatalogContext(request=request, lang=lang, chain=tuple(language_chain(lang)), currency=currency)


def plain_number(value: Decimal):
    """Whole numbers as int, otherwise float (percentages)."""
    return int(value) if value == value.to_integral_value() else float(value)


def amount_string(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.split(".")[0] if "." in text and Decimal(text) == Decimal(text).to_integral_value() else text


def money(irr: Decimal, currency: str) -> dict:
    """Stored amounts are IRR; Toman (IRT) is IRR / 10, a display unit only."""
    value = irr / Decimal(10) if currency == "IRT" else irr
    return {"amount": amount_string(value), "currency": currency}


def to_irr(amount: Decimal, currency: str) -> Decimal:
    return amount * Decimal(10) if currency == "IRT" else amount
