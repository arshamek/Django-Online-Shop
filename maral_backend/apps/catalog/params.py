"""
Strict query-parameter parsing for product lists. Unknown parameters and malformed values are rejected
with 400 `invalid_filter`; every list is bounded (max values per filter, max ids, search length, deep
paging) so a request can never trigger an unbounded query.
"""

import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.conf import settings

from apps.common.api.filters import RESERVED_QUERY_PARAMS
from apps.common.exceptions import InvalidFilter
from apps.common.text import normalize_search_text

from .context import CatalogContext, to_irr

MULTI = ("category", "brand", "source", "size", "color")
BOOLS = ("sale", "available", "is_new")
FILTER_PARAMS = frozenset({*MULTI, *BOOLS, "price_min", "price_max", "ids"})
SORTS = ("popular", "new", "price_asc", "price_desc", "discount", "relevance")
_VALUE = re.compile(r"^[\w\-.×]{1,60}$")
_TRUE, _FALSE = {"true", "1", "yes"}, {"false", "0", "no"}
MAX_PRICE = Decimal("10") ** 13


@dataclass
class ProductParams:
    categories: list = field(default_factory=list)
    brands: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    sizes: list = field(default_factory=list)
    colors: list = field(default_factory=list)
    sale: bool | None = None
    available: bool | None = None
    is_new: bool | None = None
    price_min_irr: Decimal | None = None
    price_max_irr: Decimal | None = None
    ids: list = field(default_factory=list)
    q: str = ""
    sort: str = "popular"

    def cache_parts(self) -> dict:
        return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in self.__dict__.items()}


def _multi(request, name, errors, limit):
    values: list[str] = []
    for raw in request.query_params.getlist(name):
        values.extend(part.strip().lower() for part in raw.split(",") if part.strip())
    if len(values) > limit:
        errors[name] = [f"At most {limit} values are allowed."]
        return []
    if any(not _VALUE.match(v) for v in values):
        errors[name] = ["Contains an invalid value."]
        return []
    return list(dict.fromkeys(values))


def _bool(request, name, errors):
    raw = request.query_params.get(name)
    if raw is None:
        return None
    raw = raw.lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    errors[name] = ["Use true or false."]
    return None


def _price(request, name, ctx: CatalogContext, errors):
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        errors[name] = ["Must be a number."]
        return None
    if not value.is_finite() or value < 0 or value > MAX_PRICE:
        errors[name] = ["Out of range."]
        return None
    return to_irr(value, ctx.currency)


def parse_product_params(request, ctx: CatalogContext, *, require_q=False, allow_paging=True) -> ProductParams:
    errors: dict = {}
    allowed = FILTER_PARAMS | RESERVED_QUERY_PARAMS
    unknown = sorted(set(request.query_params) - allowed)
    for name in unknown:
        errors[name] = ["Unknown filter."]

    limit = settings.CATALOG_MAX_FILTER_VALUES
    params = ProductParams(
        categories=_multi(request, "category", errors, limit),
        brands=_multi(request, "brand", errors, limit),
        sources=_multi(request, "source", errors, limit),
        sizes=_multi(request, "size", errors, limit),
        colors=_multi(request, "color", errors, limit),
        sale=_bool(request, "sale", errors),
        available=_bool(request, "available", errors),
        is_new=_bool(request, "is_new", errors),
        price_min_irr=_price(request, "price_min", ctx, errors),
        price_max_irr=_price(request, "price_max", ctx, errors),
    )
    if (
        params.price_min_irr is not None
        and params.price_max_irr is not None
        and params.price_min_irr > params.price_max_irr
    ):
        errors["price_min"] = ["price_min must not exceed price_max."]

    raw_ids = [p.strip() for raw in request.query_params.getlist("ids") for p in raw.split(",") if p.strip()]
    if len(raw_ids) > settings.CATALOG_MAX_IDS:
        errors["ids"] = [f"At most {settings.CATALOG_MAX_IDS} ids are allowed."]
    else:
        try:
            params.ids = list(dict.fromkeys(uuid.UUID(v) for v in raw_ids))
        except ValueError:
            errors["ids"] = ["Contains an invalid id."]

    raw_q = request.query_params.get("q")
    if raw_q is not None and raw_q.strip():
        if len(raw_q) > settings.CATALOG_SEARCH_MAX_CHARS:
            errors["q"] = [f"At most {settings.CATALOG_SEARCH_MAX_CHARS} characters."]
        else:
            params.q = normalize_search_text(raw_q)
            if len(params.q) < settings.CATALOG_SEARCH_MIN_CHARS:
                errors["q"] = [f"At least {settings.CATALOG_SEARCH_MIN_CHARS} characters."]
    elif require_q:
        errors["q"] = ["This parameter is required."]

    sort = (request.query_params.get("sort") or "").lower()
    if sort and sort not in SORTS:
        errors["sort"] = [f"Use one of: {', '.join(SORTS)}."]
    elif sort == "relevance" and not params.q:
        errors["sort"] = ["relevance needs q."]
    params.sort = sort or ("relevance" if params.q else "popular")

    if errors:
        raise InvalidFilter(fields=errors)
    return params
