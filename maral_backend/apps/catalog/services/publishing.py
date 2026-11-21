"""Status transitions for products (draft -> in_review -> active -> archived) with their invariants."""

from django.db import transaction
from django.utils import timezone

from apps.common.exceptions import BusinessRuleViolation

from ..models import Product, ProductStatus, ProductVariant

ALLOWED = {
    ProductStatus.DRAFT: {ProductStatus.IN_REVIEW, ProductStatus.ACTIVE, ProductStatus.ARCHIVED},
    ProductStatus.IN_REVIEW: {ProductStatus.DRAFT, ProductStatus.ACTIVE, ProductStatus.ARCHIVED},
    ProductStatus.ACTIVE: {ProductStatus.ARCHIVED, ProductStatus.DRAFT},
    ProductStatus.ARCHIVED: {ProductStatus.DRAFT},
}


def publish_blockers(product: Product) -> list[str]:
    problems = []
    if not product.translations.exists():
        problems.append("at least one translation")
    variants = ProductVariant.objects.filter(product=product, is_active=True)
    if not variants.exists():
        problems.append("at least one active variant")
    elif not variants.filter(price__isnull=False).exists():
        problems.append("a price on at least one active variant")
    if not product.brand.is_active:
        problems.append("an active brand")
    if not product.category.is_active:
        problems.append("an active category")
    return problems


@transaction.atomic
def change_status(product: Product, new_status: str) -> Product:
    if new_status == product.status:
        return product
    if new_status not in ALLOWED.get(product.status, set()):
        raise BusinessRuleViolation(
            f"Cannot move a {product.status} product to {new_status}.", code="invalid_transition"
        )
    if new_status == ProductStatus.ACTIVE:
        problems = publish_blockers(product)
        if problems:
            raise BusinessRuleViolation(
                "Product cannot be activated: it needs " + ", ".join(problems) + ".", code="not_publishable"
            )
        product.published_at = product.published_at or timezone.now()
    product.status = new_status
    product.save(update_fields=["status", "published_at", "updated_at"])
    return product
