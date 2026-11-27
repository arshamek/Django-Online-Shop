"""Builders for the Phase 9 assistant tests. Every product, price and rate here is SYNTHETIC test data."""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.brands.models import BrandAlias
from apps.product_sources.models import (
    DataOrigin,
    ExternalProduct,
    ExternalProductImage,
    ExternalProductVariant,
    SourceAvailability,
    SourcePrice,
)
from tests import catalog_factories as f
from tests import pricing_helpers as ph

API = "/api/v1"
_n = [0]

SHIPPING_CUSTOMS_RULES = [
    {
        "kind": "shipping",
        "code": "ship",
        "calc_method": "fixed",
        "amount": "3000000",
        "currency": "IRR",
        "charge_mode": "included",
    },
    {"kind": "customs", "code": "duty", "calc_method": "percent_of_base", "amount": "10", "charge_mode": "included"},
]


def sources():
    return f.source("maral"), f.source("trendyol"), f.source("namshi", country_code="AE", base_currency="AED")


def nike_brand():
    nike = f.brand("Nike")
    BrandAlias.objects.get_or_create(brand=nike, alias="نایک", alias_normalised="نایک")
    return nike


def internal_sneaker(
    name="کتونی نایک ایر زوم پیاده‌روی", size="42", color="black", final=4_000_000, available=True, slug=None
):
    nike = nike_brand()
    cat = f.category("sneakers", fa="کتونی")
    return f.product(
        slug,
        names={"fa": name},
        brand_obj=nike,
        category_obj=cat,
        variants=[
            {
                "size": size,
                "color": color,
                "final": final,
                "available": available,
                "fulfillment": "DIRECT",
                "source_code": "maral",
            }
        ],
    )


def snapshot(
    code="trendyol",
    ext_id="T1",
    title="Nike Air Zoom Running Sneaker",
    brand="Nike",
    variants=(("42", "black", "1000", "in_stock"),),
    origin=DataOrigin.LIVE,
    price_age_hours=0,
    canonical=None,
    match="unmatched",
    description="Light running sneaker",
    url="https://www.trendyol.com/p-1",
    images=True,
    **extra,
):
    src = f.source(code)
    ext = ExternalProduct.objects.create(
        source=src,
        external_id=ext_id,
        raw_title=title,
        raw_brand=brand,
        brand_key=brand.lower(),
        raw_category_path="Shoes > Sneakers",
        raw_description=description,
        external_url=url,
        data_origin=origin,
        canonical_product=canonical,
        match_status=match,
        **extra,
    )
    if images:
        ExternalProductImage.objects.create(external_product=ext, url="https://img.test/1.jpg", alt="sneaker")
    currency = src.base_currency
    for i, (size, color, price, stock) in enumerate(variants):
        _n[0] += 1
        ev = ExternalProductVariant.objects.create(
            external_product=ext,
            external_variant_id=f"{ext_id}-V{i}",
            size_raw=size,
            color_raw=color,
            size_key=size.lower(),
            color_key=color.lower(),
        )
        if price is not None:
            sp = SourcePrice.objects.create(
                variant=ev,
                list_amount=Decimal(price),
                currency_code=currency,
                value_hash=f"h{_n[0]}",
                observed_at=timezone.now() - timedelta(hours=price_age_hours),
            )
            ev.current_price = sp
        if stock:
            av = SourceAvailability.objects.create(
                variant=ev, status=stock, observed_at=timezone.now() - timedelta(hours=price_age_hours)
            )
            ev.current_availability = av
        ev.save()
    return ext


def pricing(with_costs=True, **decisions):
    """Fresh synthetic TRY/AED rates + a published rule set (with or without shipping/customs rules)."""
    ph.fx({"TRY": "15000", "AED": "150000", "USD": "550000", "EUR": "600000"})
    return ph.make_ruleset(rules=SHIPPING_CUSTOMS_RULES if with_costs else [], **decisions)


def enable_snapshots(fixture=True):
    from apps.ai_assistant.models import AssistantConfig

    config = AssistantConfig.load()
    config.allow_fixture_snapshots = fixture
    config.save()
    return config


def chat(client, message, **extra):
    return client.post(f"{API}/ai/chat", {"message": message, **extra}, format="json")
