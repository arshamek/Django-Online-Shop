"""Builds a PriceInput from a catalogue variant and its source listing."""

from decimal import Decimal

from apps.product_sources.models import ExternalProductVariant

from ..models import PricingRuleSet, SourcePriceBasis
from .engine import PriceInput


def source_listing(variant):
    """The supplier listing that prices this variant: the variant's own source (no cross-supplier optimisation, B-14)."""
    return (
        ExternalProductVariant.objects.filter(
            canonical_variant=variant, external_product__source_id=variant.source_id, is_active=True
        )
        .select_related("current_price", "external_product")
        .order_by("id")
        .first()
    )


def effective_amounts(price, basis: str) -> tuple[Decimal, Decimal | None]:
    """(amount the cost is based on, list amount for the discount display)."""
    if basis == SourcePriceBasis.LIST:
        effective = price.list_amount
    elif basis == SourcePriceBasis.SALE_OR_LIST:
        effective = price.sale_amount if price.sale_amount is not None else price.list_amount
    else:
        effective = next(a for a in (price.customer_amount, price.sale_amount, price.list_amount) if a is not None)
    return effective, price.list_amount


def build_input(
    variant,
    rule_set: PricingRuleSet,
    *,
    destination_country="",
    sales_model="",
    shipping_method="",
    presentation_currency="IRT",
    at=None,
) -> tuple[PriceInput | None, str]:
    """Returns (input, "") or (None, reason) when the variant has nothing to price from."""
    listing = source_listing(variant)
    if listing is None or listing.current_price is None:
        return None, "no_source_price"
    price = listing.current_price
    effective, list_amount = effective_amounts(price, rule_set.source_price_basis)
    product = variant.product
    return (
        PriceInput(
            original_amount=effective,
            original_currency=price.currency_code,
            list_amount=list_amount,
            variant_id=variant.pk,
            product_id=product.pk,
            brand_id=product.brand_id,
            category_id=product.category_id,
            category_path=product.category.path,
            source_id=variant.source_id,
            source_country=variant.source.country_code,
            fulfillment_type=variant.fulfillment_type,
            weight_grams=variant.weight_grams,
            destination_country=destination_country or rule_set.default_destination_country,
            sales_model=sales_model,
            shipping_method=shipping_method,
            presentation_currency=presentation_currency,
            at=at,
        ),
        "",
    )
