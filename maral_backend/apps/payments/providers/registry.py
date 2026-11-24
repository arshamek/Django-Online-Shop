from django.conf import settings

from apps.common.exceptions import BusinessRuleViolation

from .base import PaymentProvider
from .fake import FakeProvider


def available() -> dict[str, PaymentProvider]:
    providers: dict[str, PaymentProvider] = {}
    if settings.PAYMENTS_ALLOW_FAKE_PROVIDER:
        providers["fake"] = FakeProvider()
    return providers


def get(key: str = "") -> PaymentProvider:
    key = key or settings.PAYMENTS_DEFAULT_PROVIDER
    providers = available()
    if not key and len(providers) == 1:
        key = next(iter(providers))
    provider = providers.get(key)
    if provider is None:
        raise BusinessRuleViolation("No payment provider is configured.", code="payment_provider_unavailable")
    return provider
