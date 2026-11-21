"""
`ProductListingProjection` writer. The projection row exists exactly while a product is publicly visible:
status active, brand + category (and its ancestors) active, and >= 1 active variant that has a price.
"""

from collections.abc import Iterable

import structlog
from django.db import transaction
from django.utils import timezone

from apps.categories.models import Category

from ..models import (
    AvailabilityStatus,
    FulfillmentSummary,
    FulfillmentType,
    Product,
    ProductListingProjection,
    ProductListingSource,
    ProductStatus,
    ProductVariant,
)

logger = structlog.get_logger("maral.catalog")


def _category_chain_active(category: Category) -> bool:
    if not category.is_active:
        return False
    ancestor_ids = [int(part) for part in category.path.strip("/").split("/") if part and int(part) != category.pk]
    return not Category.objects.filter(pk__in=ancestor_ids, is_active=False).exists() if ancestor_ids else True


def _remove(product_id: int) -> None:
    ProductListingProjection.objects.filter(pk=product_id).delete()
    ProductListingSource.objects.filter(product_id=product_id).delete()


@transaction.atomic
def refresh_projection(product_id: int) -> bool:
    """Rebuild one product's projection. Returns True when the product is publicly visible."""
    product = Product.objects.select_related("brand", "category").filter(pk=product_id).first()
    if product is None:
        _remove(product_id)
        return False
    if (
        product.status != ProductStatus.ACTIVE
        or not product.brand.is_active
        or not _category_chain_active(product.category)
    ):
        _remove(product_id)
        return False

    variants = list(
        ProductVariant.objects.filter(product_id=product_id, is_active=True)
        .select_related("price", "availability", "source")
        .order_by("sort_order", "id")
    )
    priced = [v for v in variants if getattr(v, "price", None) is not None]
    if not priced:
        _remove(product_id)
        return False

    def sellable(v) -> bool:
        availability = getattr(v, "availability", None)
        return availability is not None and availability.status == AvailabilityStatus.IN_STOCK

    sellable_variants = [v for v in priced if sellable(v)]
    pool = sellable_variants or priced
    display = min(pool, key=lambda v: (v.price.final_irr, v.sort_order, v.id))
    types = {v.fulfillment_type for v in pool}
    summary = (
        FulfillmentSummary.MIXED
        if len(types) > 1
        else FulfillmentSummary.DIRECT
        if FulfillmentType.DIRECT_INVENTORY in types
        else FulfillmentSummary.PROXY
    )
    finals = [v.price.final_irr for v in priced]
    ProductListingProjection.objects.update_or_create(
        product=product,
        defaults={
            "brand": product.brand,
            "category": product.category,
            "category_path": product.category.path,
            "new_until": product.new_until,
            "display_variant": display,
            "display_source": display.source,
            "display_original_amount": display.price.original_amount,
            "display_original_currency": display.price.original_currency,
            "price_list_irr": display.price.list_irr,
            "price_final_irr": display.price.final_irr,
            "discount_percent": display.price.discount_percent,
            "min_final_irr": min(finals),
            "max_final_irr": max(finals),
            "is_available": bool(sellable_variants),
            "fulfillment_summary": summary,
            "has_direct_stock": any(v.fulfillment_type == FulfillmentType.DIRECT_INVENTORY for v in sellable_variants),
            "popularity_score": product.popularity_score,
            "refreshed_at": timezone.now(),
        },
    )
    wanted = {v.source_id for v in priced}
    current = set(ProductListingSource.objects.filter(product_id=product_id).values_list("source_id", flat=True))
    ProductListingSource.objects.filter(product_id=product_id, source_id__in=current - wanted).delete()
    ProductListingSource.objects.bulk_create(
        [ProductListingSource(product_id=product_id, source_id=s) for s in wanted - current]
    )
    return True


def refresh_many(product_ids: Iterable[int]) -> int:
    visible = 0
    for product_id in product_ids:
        visible += refresh_projection(product_id)
    return visible


def rebuild_all(batch: int = 500) -> dict:
    """Rebuild every projection (and drop orphans). Safe to run any time; used by the management command."""
    ids = list(Product.objects.values_list("pk", flat=True))
    visible = refresh_many(ids)
    orphans = ProductListingProjection.objects.exclude(product_id__in=ids).delete()[0]
    logger.info("catalog_projection_rebuilt", products=len(ids), visible=visible, orphans=orphans)
    return {"products": len(ids), "visible": visible, "orphans_removed": orphans}
