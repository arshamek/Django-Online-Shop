"""IMPORTS_PRICING_HANDLER: called by the import pipeline for variants that have no price yet."""

from .models import PriceCause
from .tasks import reprice_variants


def import_pricing_handler(product_id: int, variant_ids: list[int]) -> None:
    if variant_ids:
        reprice_variants.delay(list(variant_ids), cause=PriceCause.INITIAL)
