"""IMPORTS_TRANSLATION_HANDLER: queue translations of a freshly imported product (and its colours/categories)."""

from .models import EntityType
from .services import jobs


def import_translation_handler(product_id: int, targets: list[str]) -> None:
    from apps.catalog.models import Product, ProductVariantOption

    jobs.enqueue(EntityType.PRODUCT, product_id, targets)
    product = Product.objects.filter(pk=product_id).select_related("category").first()
    if product is None:
        return
    jobs.enqueue(EntityType.CATEGORY, product.category_id, targets)
    option_ids = set(
        ProductVariantOption.objects.filter(variant__product_id=product_id).values_list("option_id", flat=True)
    )
    for option_id in option_ids:
        jobs.enqueue(EntityType.OPTION, option_id, targets)
