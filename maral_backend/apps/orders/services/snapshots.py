"""Builds the immutable OrderItem snapshot from the accepted quote line + the variant as it is at order time."""

from decimal import Decimal

from apps.catalog.models import ProductImage
from apps.catalog.presenters import delivery_estimate
from apps.pricing.services import inputs

from ..models import OrderItem


def _pick_name(translations, preferred=("fa",), fallback=""):
    by_lang = {t.language: t for t in translations}
    for code in preferred:
        if code in by_lang:
            return by_lang[code]
    return next(iter(by_lang.values()), None)


def _option(variant, attribute_code):
    for vo in variant.options.all():
        if vo.attribute.code == attribute_code:
            t = _pick_name(list(vo.option.translations.all()))
            return vo.option.code, (t.name if t else vo.option.code)
    return "", ""


def _component_sum(quote_item, *kinds) -> Decimal:
    return sum((c.amount_irr for c in quote_item.components.all() if c.kind in kinds), Decimal(0))


def build_item(order, variant, quote_item, quantity: int) -> OrderItem:
    product = variant.product
    t = _pick_name(list(product.translations.all()), ("fa", product.source_language))
    size_code, size_name = _option(variant, "size")
    color_code, color_name = _option(variant, "color")
    image = ProductImage.objects.published().filter(product=product).order_by("sort_order", "id").first()
    price = getattr(variant, "price", None)
    listing = inputs.source_listing(variant) if variant.fulfillment_type == "PROXY_PURCHASE" else None
    estimate = delivery_estimate(variant.fulfillment_type, variant.source.code)
    requirements: dict = {}
    if listing is not None:
        requirements = {
            "sku": variant.sku,
            "source_sku": listing.sku or variant.source_sku,
            "size": size_code,
            "color": color_code,
            "quantity": quantity,
            "max_source_amount": str(quote_item.original_amount),  # what the customer's price was built on
            "currency": quote_item.original_currency,
            "on_price_increase": "hold_and_contact_customer",
        }
    list_unit = price.list_irr if price is not None else quote_item.unit_price_irr
    return OrderItem(
        order=order,
        variant=variant,
        quote_item=quote_item,
        product_name=(t.name if t else product.slug)[:255],
        brand_name=product.brand.name[:160],
        category_slug=product.category.slug,
        sku=variant.sku,
        size_code=size_code,
        size_name=size_name,
        color_code=color_code,
        color_name=color_name,
        variant_options={"size": size_code, "color": color_code},
        image_url=(image.image.url if image and image.image else "")[:500],
        quantity=quantity,
        fulfillment_type=variant.fulfillment_type,
        source=variant.source,
        source_code=variant.source.code,
        source_product_id=listing.external_product.external_id if listing else "",
        source_variant_id=listing.external_variant_id if listing else "",
        source_product_url=((listing.source_url or listing.external_product.external_url) if listing else "")[:500],
        purchase_requirements=requirements,
        expected_days_min=estimate["min_days"] if estimate else None,
        expected_days_max=estimate["max_days"] if estimate else None,
        expected_days_unit=estimate["unit"] if estimate else "",
        original_amount=quote_item.original_amount,
        original_currency=quote_item.original_currency,
        exchange_rate=quote_item.rate_applied,
        exchange_rate_source=quote_item.rate_source,
        exchange_rate_at=quote_item.rate_at,
        exchange_rate_is_manual=quote_item.rate_is_manual,
        list_unit_irr=list_unit,
        unit_price_irr=quote_item.unit_price_irr,
        discount_unit_irr=max(list_unit - quote_item.unit_price_irr, Decimal(0)),
        fees_unit_irr=_component_sum(quote_item, "purchase_fee", "service_fee"),
        freight_unit_irr=_component_sum(quote_item, "shipping"),
        customs_unit_irr=_component_sum(quote_item, "customs"),
        unit_separate_irr=quote_item.unit_separate_irr,
        line_price_irr=quote_item.line_price_irr,
        line_separate_irr=quote_item.line_separate_irr,
        line_total_irr=quote_item.line_total_irr,
        has_estimated_costs=quote_item.has_estimated_costs,
        pricing_rule_version=quote_item.rule_version,
    )
