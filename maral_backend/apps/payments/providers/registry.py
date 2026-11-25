"""
Which adapters exist in this process. Mocks appear only when `PAYMENTS_ALLOW_FAKE_PROVIDER` is on (never in production);
real adapters are added by listing their dotted path in `PAYMENT_PROVIDER_ADAPTERS` (no code change elsewhere).
Existence of an adapter does NOT make it usable: see `payments.methods` (admin configuration + verified eligibility).
"""

from django.conf import settings
from django.utils.module_loading import import_string

from apps.common.exceptions import BusinessRuleViolation

from .base import BasePaymentProvider
from .fake import FakeProvider
from .mock import MockInternationalProvider, MockIranianGateway

MOCK_ADAPTERS = (FakeProvider, MockIranianGateway, MockInternationalProvider)


def adapters() -> dict[str, BasePaymentProvider]:
    found: dict[str, BasePaymentProvider] = {}
    if settings.PAYMENTS_ALLOW_FAKE_PROVIDER:
        for cls in MOCK_ADAPTERS:
            found[cls.key] = cls()
    for dotted in getattr(settings, "PAYMENT_PROVIDER_ADAPTERS", []) or []:
        instance = import_string(dotted)()
        if instance.capabilities.is_mock and not settings.PAYMENTS_ALLOW_FAKE_PROVIDER:
            continue
        if not instance.key or instance.key in found:
            raise RuntimeError(f"Payment adapter {dotted} has an empty or duplicate key {instance.key!r}.")
        found[instance.key] = instance
    return found


available = adapters  # Phase 7 name


def get(key: str = "") -> BasePaymentProvider:
    key = key or settings.PAYMENTS_DEFAULT_PROVIDER
    providers = adapters()
    if not key and len(providers) == 1:
        key = next(iter(providers))
    provider = providers.get(key)
    if provider is None:
        raise BusinessRuleViolation("No payment provider is configured.", code="payment_provider_unavailable")
    return provider
