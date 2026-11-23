"""
Which rate applies? Never guessed: an administrator-configured RateRoute decides.

Order: sources by priority; the first with a *fresh* accepted rate wins (a manual source placed first acts as an
override and falls through automatically once its rate expires). If nothing is fresh: refuse (`rate_stale`), or, only
when the route allows it, return the freshest stale rate flagged `is_stale`. No route -> `no_rate_route`.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone

from ..errors import RateError
from ..models import Currency, ExchangeRate, RateRoute, RateStatus


@dataclass(frozen=True)
class ResolvedRate:
    base: str
    quote: str  # route quote currency (IRR)
    rate: Decimal  # quote units per 1 base unit, already normalised (a Toman quote is multiplied by its factor)
    purpose: str
    source_code: str
    source_kind: str
    rate_id: int | None
    rate_at: datetime | None
    fetched_at: datetime | None
    usable_until: datetime | None
    is_manual: bool
    is_stale: bool
    is_fallback: bool
    quoted_in: str  # currency the provider quoted in (IRT when the source speaks Toman)
    raw_rate: Decimal

    def as_dict(self) -> dict:
        return {
            "base": self.base,
            "quote": self.quote,
            "rate": str(self.rate),
            "purpose": self.purpose,
            "source": self.source_code,
            "source_kind": self.source_kind,
            "rate_id": self.rate_id,
            "rate_at": self.rate_at.isoformat() if self.rate_at else None,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "usable_until": self.usable_until.isoformat() if self.usable_until else None,
            "is_manual": self.is_manual,
            "is_stale": self.is_stale,
            "is_fallback": self.is_fallback,
            "quoted_in": self.quoted_in,
            "raw_rate": str(self.raw_rate),
        }


def _identity(base: str, quote: str, purpose: str, factor: Decimal, source: str) -> ResolvedRate:
    return ResolvedRate(
        base, quote, factor, purpose, source, "identity", None, None, None, None, False, False, False, base, factor
    )


def resolve_rate(base: str, quote: str = "IRR", purpose: str = "purchase", at: datetime | None = None) -> ResolvedRate:
    at = at or timezone.now()
    base_cur = Currency.objects.filter(code=base).first()
    quote_cur = Currency.objects.filter(code=quote).first()
    if base_cur is None or quote_cur is None:
        raise RateError("unknown_currency", f"Unknown currency in {base}->{quote}.")
    if base == quote:
        return _identity(base, quote, purpose, Decimal(1), "identity")
    if base_cur.base_currency_id == quote:  # IRT -> IRR is a unit conversion, not a market rate
        return _identity(base, quote, purpose, base_cur.base_factor, "unit")

    route = RateRoute.objects.filter(base=base, quote=quote, purpose=purpose).first()
    if route is None:
        raise RateError("no_rate_route", f"No rate route is configured for {base}->{quote} ({purpose}).")
    if not route.is_enabled:
        raise RateError("rate_route_disabled", f"The rate route {base}->{quote} ({purpose}) is disabled.")

    quote_codes = [quote, *Currency.objects.filter(base_currency_id=quote).values_list("code", flat=True)]
    factors = {quote: Decimal(1), **{c.code: c.base_factor for c in Currency.objects.filter(base_currency_id=quote)}}
    max_age = timedelta(seconds=route.max_age_seconds)

    stale_best = None
    first_priority = None
    for link in route.route_sources.select_related("source").all():
        if not link.source.is_enabled:
            continue
        first_priority = link.priority if first_priority is None else first_priority
        row = (
            ExchangeRate.objects.filter(
                source=link.source, base=base, quote__in=quote_codes, status=RateStatus.ACCEPTED, rate_at__lte=at
            )
            .select_related("source")
            .order_by("-rate_at", "-id")
            .first()
        )
        if row is None:
            continue
        usable_until = row.valid_until or (row.rate_at + max_age)
        if row.valid_until and row.valid_until > row.rate_at + timedelta(days=366):
            usable_until = row.rate_at + timedelta(days=366)
        candidate = (row, usable_until, link.priority != first_priority)
        if usable_until > at:
            return _build(row, quote, purpose, factors[row.quote_id], usable_until, False, candidate[2])
        if stale_best is None or row.rate_at > stale_best[0].rate_at:
            stale_best = candidate
    if stale_best is not None:
        if route.allow_stale_fallback:
            row, usable_until, fallback = stale_best
            return _build(row, quote, purpose, factors[row.quote_id], usable_until, True, fallback)
        raise RateError(
            "rate_stale", f"The latest {base}->{quote} rate is older than the allowed {route.max_age_seconds}s."
        )
    raise RateError("rate_missing", f"No accepted {base}->{quote} rate exists for the configured sources.")


def _build(row, quote, purpose, factor, usable_until, is_stale, is_fallback) -> ResolvedRate:
    return ResolvedRate(
        base=row.base_id,
        quote=quote,
        rate=(row.rate * factor),
        purpose=purpose,
        source_code=row.source.code,
        source_kind=row.source.kind,
        rate_id=row.pk,
        rate_at=row.rate_at,
        fetched_at=row.fetched_at,
        usable_until=usable_until,
        is_manual=row.is_manual,
        is_stale=is_stale,
        is_fallback=is_fallback,
        quoted_in=row.quote_id,
        raw_rate=row.rate,
    )
