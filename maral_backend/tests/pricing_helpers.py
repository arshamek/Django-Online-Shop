"""Builders for the Phase 6 financial tests. All numbers are SYNTHETIC (tests/fixtures/pricing_scenarios.json)."""

import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.utils import timezone

from apps.currencies.models import Currency, ExchangeRate, ExchangeRateSource, RateRoute, RateRouteSource
from apps.pricing.models import PricingRule, PricingRuleSet
from apps.pricing.services import rulesets

SCENARIOS = json.loads((Path(__file__).parent / "fixtures" / "pricing_scenarios.json").read_text())
D = Decimal


def source(code="bureau", kind="bureau", **extra):
    obj, _ = ExchangeRateSource.objects.get_or_create(
        code=code, defaults={"name": code, "provider_key": "fixture", "kind": kind, **extra}
    )
    return obj


def route(base, sources=("bureau",), max_age=3600, purpose="purchase", quote="IRR", **extra):
    obj, _ = RateRoute.objects.update_or_create(
        base_id=base, quote_id=quote, purpose=purpose, defaults={"max_age_seconds": max_age, **extra}
    )
    obj.route_sources.all().delete()
    for i, code in enumerate(sources):
        RateRouteSource.objects.create(route=obj, source=source(code), priority=(i + 1) * 10)
    return obj


def rate(base, value, src="bureau", age_seconds=0, quote="IRR", now=None, **extra):
    now = now or timezone.now()
    return ExchangeRate.objects.create(
        source=source(src),
        base_id=base,
        quote_id=quote,
        rate=D(str(value)),
        rate_at=now - timedelta(seconds=age_seconds),
        fetched_at=now,
        **extra,
    )


def fx(rates=None, max_age=3600, display=("USD", "EUR", "TRY", "AED")):
    """Routes + fresh rates for the four source currencies (synthetic numbers)."""
    for code, value in (rates or SCENARIOS["rates_irr"]).items():
        route(code, max_age=max_age)
        rate(code, value)
        if code in display:
            route(code, max_age=max_age, purpose="display")
    return Currency.objects.all()


def make_rule(rule_set, kind, code, **kw):
    kw.setdefault("name", code)
    kw.setdefault("value_status", "confirmed")
    kw.setdefault("source_note", "test fixture")
    if kind in ("shipping", "customs", "purchase_fee", "service_fee"):
        kw.setdefault("charge_mode", "included")
    for key in ("currency", "source", "category", "brand", "product"):
        if isinstance(kw.get(key), str) and key == "currency":
            kw["currency_id"] = kw.pop("currency")
    if "amount" in kw and kw["amount"] is not None:
        kw["amount"] = D(str(kw["amount"]))
    for key in ("min_irr", "max_irr", "rounding_step_irr"):
        if kw.get(key) is not None:
            kw[key] = D(str(kw[key]))
    rule = PricingRule(rule_set=rule_set, kind=kind, code=code, **kw)
    rule.clean()
    rule.save()
    return rule


def make_ruleset(*, rules=(), publish=True, **decisions):
    decisions = {
        "profit_method": "markup",
        "target_profit_percent": D("25"),
        "target_profit_confirmed": True,
        "estimates_policy": "allow",
        "required_cost_kinds": [],
        "required_for_fulfillment": [],
        **decisions,
    }
    if decisions["target_profit_percent"] is not None:
        decisions["target_profit_percent"] = D(str(decisions["target_profit_percent"]))
    rs = PricingRuleSet.objects.create(**decisions)
    for spec in rules:
        spec = dict(spec)
        make_rule(rs, spec.pop("kind"), spec.pop("code"), **spec)
    return rulesets.publish(rs) if publish else rs


def scenario_ruleset(scenario, publish=False):
    return make_ruleset(rules=scenario["rules"], publish=publish, **scenario["ruleset"])


# ---------------------------------------------------------------------------------------- catalogue helpers
def priced_variant(
    amount="1000",
    currency="TRY",
    *,
    list_amount=None,
    sale=None,
    customer=None,
    weight=None,
    price=False,
    fulfillment="PROXY_PURCHASE",
):
    """An active variant whose supplier listing carries a source price (what the repricing engine reads)."""

    from apps.catalog.models import ProductVariant
    from apps.product_sources.models import ExternalProduct, ExternalProductVariant, SourcePrice
    from tests import catalog_factories as f

    n = next(_COUNTER)
    product = f.product(f"fin-{n}", variants=[{"price": price, "fulfillment": fulfillment, "sku": f"FIN-{n}"}])
    variant = product.variants.get()
    if weight is not None:
        ProductVariant.objects.filter(pk=variant.pk).update(weight_grams=weight)
        variant.refresh_from_db()
    ext = ExternalProduct.objects.create(source=variant.source, external_id=f"E{n}", raw_title="x")
    ev = ExternalProductVariant.objects.create(
        external_product=ext, external_variant_id=f"EV{n}", canonical_variant=variant
    )
    sp = SourcePrice.objects.create(
        variant=ev,
        list_amount=D(str(list_amount or amount)),
        sale_amount=D(str(sale)) if sale else None,
        customer_amount=D(str(customer)) if customer else None,
        currency_code=currency,
        value_hash=f"h{n}",
    )
    ev.current_price = sp
    ev.save(update_fields=["current_price"])
    return variant


def change_source_price(variant, amount, currency="TRY"):
    from apps.product_sources.models import ExternalProductVariant, SourcePrice

    ev = ExternalProductVariant.objects.get(canonical_variant=variant)
    sp = SourcePrice.objects.create(
        variant=ev, list_amount=D(str(amount)), currency_code=currency, value_hash=f"h{next(_COUNTER)}"
    )
    ev.current_price = sp
    ev.save(update_fields=["current_price"])


import itertools  # noqa: E402

_COUNTER = itertools.count(1)
