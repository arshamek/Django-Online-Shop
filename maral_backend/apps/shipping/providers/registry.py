from django.conf import settings
from django.utils.module_loading import import_string

from .base import BaseShippingProvider
from .mock import MockCarrierProvider
from .table import TableRateProvider


def adapters() -> dict[str, BaseShippingProvider]:
    found: dict[str, BaseShippingProvider] = {TableRateProvider.key: TableRateProvider()}
    if settings.SHIPPING_ALLOW_MOCK_CARRIER:
        found[MockCarrierProvider.key] = MockCarrierProvider()
    for dotted in getattr(settings, "SHIPPING_PROVIDER_ADAPTERS", []) or []:
        instance = import_string(dotted)()
        if instance.capabilities.is_mock and not settings.SHIPPING_ALLOW_MOCK_CARRIER:
            continue
        if not instance.key or instance.key in found:
            raise RuntimeError(f"Shipping adapter {dotted} has an empty or duplicate key {instance.key!r}.")
        found[instance.key] = instance
    return found


def get(key: str) -> BaseShippingProvider | None:
    return adapters().get(key or "table")
