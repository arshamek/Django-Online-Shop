"""ExchangeRateProvider port. Adapters return observations; validation and storage are done by services/refresh.py."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class RateObservation:
    base: str
    quote: str
    rate: Decimal
    as_of: datetime | None  # the provider's own timestamp; None = provider gave none (rejected unless trusted)
    raw: dict = field(default_factory=dict)


class ExchangeRateProvider(ABC):
    key = ""
    LIVE_VERIFIED = False  # no adapter has been exercised against a live service in this repository

    @abstractmethod
    def fetch(self, source) -> list[RateObservation]:
        """Return observations or raise ProviderError. Must not write to the database."""
