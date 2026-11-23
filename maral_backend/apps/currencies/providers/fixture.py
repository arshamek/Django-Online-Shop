"""Synthetic numbers for development and tests. Refused unless settings.CURRENCY_ALLOW_FIXTURE_PROVIDER."""

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..errors import ProviderError
from .base import ExchangeRateProvider, RateObservation


class FixtureProvider(ExchangeRateProvider):
    """config = {"rates": [{"base": "TRY", "quote": "IRR", "rate": "1500000", "as_of": "now" | ISO-8601}]}"""

    key = "fixture"

    def fetch(self, source):
        if not settings.CURRENCY_ALLOW_FIXTURE_PROVIDER:
            raise ProviderError(
                "fixture_not_allowed", "Fixture rates are disabled in this environment.", transient=False
            )
        out = []
        for item in (source.config or {}).get("rates", []):
            try:
                rate = Decimal(str(item["rate"]))
            except (InvalidOperation, KeyError):
                raise ProviderError("bad_fixture", "Invalid fixture rate.", transient=False) from None
            raw_ts = item.get("as_of", "now")
            as_of = timezone.now() if raw_ts == "now" else parse_datetime(raw_ts) if raw_ts else None
            out.append(RateObservation(item["base"], item["quote"], rate, as_of, {"fixture": True}))
        return out
