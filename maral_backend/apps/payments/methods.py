"""
Which payment methods a customer may use, and what exactly would be charged.

A mock adapter is usable when mocks are allowed (an explicit disable in its config still wins). A REAL adapter is usable
only when an administrator enabled it AND recorded that merchant eligibility was verified; live mode additionally needs
a recorded sandbox verification. Nothing is inferred from the adapter's existence.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from apps.common.exceptions import BusinessRuleViolation
from apps.currencies.errors import RateError
from apps.currencies.models import Currency
from apps.currencies.services.resolver import resolve_rate

from .models import EligibilityStatus, PaymentProviderConfig, ProviderMode
from .providers import registry
from .providers.base import BasePaymentProvider


class MethodUnavailable(BusinessRuleViolation):
    default_code = "payment_method_unavailable"
    default_detail = "This payment method is not available."


@dataclass
class ChargePlan:
    adapter: BasePaymentProvider
    config: PaymentProviderConfig | None
    currency: str
    amount: Decimal
    amount_irr: Decimal
    fx_rate: Decimal | None
    fx_source: str
    mode: str


def _config(key: str) -> PaymentProviderConfig | None:
    return PaymentProviderConfig.objects.filter(key=key).first()


def unavailable_reason(adapter: BasePaymentProvider, config, *, amount_irr: Decimal, country: str) -> str:
    """'' when usable, otherwise a machine-readable reason (never shown with internal notes)."""
    if adapter.capabilities.is_mock:
        if config is not None and not config.is_enabled:
            return "disabled"
    else:
        if config is None or not config.is_enabled:
            return "disabled"
        if config.eligibility_status != EligibilityStatus.VERIFIED:
            return "eligibility_not_verified"
        if config.mode == ProviderMode.LIVE and config.sandbox_verified_at is None:
            return "sandbox_not_verified"
    if config is not None:
        if config.customer_countries and country.upper() not in [c.upper() for c in config.customer_countries]:
            return "country_not_supported"
        if config.min_amount_irr is not None and amount_irr < config.min_amount_irr:
            return "amount_below_minimum"
        if config.max_amount_irr is not None and amount_irr > config.max_amount_irr:
            return "amount_above_maximum"
    return ""


def _candidates(adapter, config) -> list[str]:
    adapter_currencies = list(adapter.supported_currencies())
    wanted = (
        list(config.supported_currencies) if config is not None and config.supported_currencies else adapter_currencies
    )
    return [c for c in wanted if c in adapter_currencies]


def _charge_currency(adapter, config, candidates: list[str], display_currency: str) -> str:
    display = (display_currency or "").upper()
    if "IRR" in candidates and (adapter.capabilities.kind == "iranian" or display in ("IRR", "IRT")):
        return "IRR"
    if display in candidates:
        return display
    default = (config.default_charge_currency if config else "").upper()
    return default if default in candidates else candidates[0]


def plan(adapter, config, *, amount_irr: Decimal, display_currency: str, country: str) -> ChargePlan:
    reason = unavailable_reason(adapter, config, amount_irr=amount_irr, country=country)
    if reason:
        raise MethodUnavailable(code=f"payment_method_{reason}")
    candidates = _candidates(adapter, config)
    if not candidates:
        raise MethodUnavailable(code="payment_method_no_currency")
    currency = _charge_currency(adapter, config, candidates, display_currency)
    mode = "mock" if adapter.capabilities.is_mock else (config.mode if config else "")
    if currency == "IRR":
        return ChargePlan(adapter, config, "IRR", amount_irr, amount_irr, None, "", mode)
    cur = Currency.objects.filter(code=currency, is_active=True).first()
    if cur is None:
        raise MethodUnavailable(code="payment_rate_unavailable")
    try:
        rate = resolve_rate(currency, "IRR", "display")
    except RateError:
        raise MethodUnavailable(code="payment_rate_unavailable") from None
    if rate.is_stale:  # never charge a customer on a stale rate
        raise MethodUnavailable(code="payment_rate_stale")
    quantum = Decimal(1).scaleb(-cur.decimal_places)
    amount = (amount_irr / rate.rate).quantize(quantum, rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise MethodUnavailable(code="payment_amount_too_small")
    return ChargePlan(adapter, config, currency, amount, amount_irr, rate.rate, rate.source_code, mode)


def resolve(provider_key: str, *, amount_irr: Decimal, display_currency: str, country: str) -> ChargePlan:
    adapter = registry.get(provider_key)
    return plan(
        adapter, _config(adapter.key), amount_irr=amount_irr, display_currency=display_currency, country=country
    )


def list_methods(*, amount_irr: Decimal, display_currency: str, country: str, lang: str = "fa") -> list[dict]:
    """Every adapter that is usable for this amount / country, with what would be charged. Safe for customers."""
    out = []
    configs = {c.key: c for c in PaymentProviderConfig.objects.all()}
    for key, adapter in registry.adapters().items():
        config = configs.get(key)
        try:
            charge = plan(adapter, config, amount_irr=amount_irr, display_currency=display_currency, country=country)
        except MethodUnavailable:
            continue
        names = (config.names if config and config.names else adapter.default_names) or {}
        out.append(
            {
                "key": key,
                "kind": adapter.capabilities.kind,
                "name": names.get(lang) or names.get("fa") or names.get("en") or key,
                "charge": {"amount": str(charge.amount), "currency": charge.currency},
                "is_foreign_currency": charge.currency != "IRR",
                "exchange_rate": None if charge.fx_rate is None else {"rate": str(charge.fx_rate), "quote": "IRR"},
                "is_test_method": adapter.capabilities.is_mock,
                "_sort": (config.sort_order if config else 1000, key),
            }
        )
    out.sort(key=lambda m: m.pop("_sort"))
    return out
