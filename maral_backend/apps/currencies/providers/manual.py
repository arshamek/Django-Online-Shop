from .base import ExchangeRateProvider


class ManualProvider(ExchangeRateProvider):
    """Manual sources are never polled: staff enter rates through services.manual.set_manual_rate."""

    key = "manual"

    def fetch(self, source):
        return []
