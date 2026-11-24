"""Wishlist: customer-only, product-level (the frontend heart toggles a product, not a size)."""

from django.conf import settings
from django.db import IntegrityError, transaction

from apps.catalog.models import Product
from apps.common.exceptions import BusinessRuleViolation

from .models import Wishlist, WishlistItem


def for_user(user) -> Wishlist:
    wishlist, _ = Wishlist.objects.get_or_create(user=user)
    return wishlist


def visible_product(public_id) -> Product:
    product = Product.objects.visible().filter(public_id=public_id).first()
    if product is None:
        raise BusinessRuleViolation("Product not found.", code="product_not_found")
    return product


def add(user, product: Product) -> bool:
    """True when newly added; adding twice is a no-op (idempotent PUT)."""
    wishlist = for_user(user)
    if WishlistItem.objects.filter(wishlist=wishlist, product=product).exists():
        return False
    if wishlist.items.count() >= settings.WISHLIST_MAX_ITEMS:
        raise BusinessRuleViolation(f"A wishlist holds at most {settings.WISHLIST_MAX_ITEMS} products.", code="wishlist_full")
    try:
        with transaction.atomic():
            WishlistItem.objects.create(wishlist=wishlist, product=product)
    except IntegrityError:  # concurrent double click
        return False
    return True


def remove(user, product_public_id) -> bool:
    deleted, _ = WishlistItem.objects.filter(wishlist__user=user, product__public_id=product_public_id).delete()
    return bool(deleted)


def status_for(user, product_public_ids) -> dict[str, bool]:
    have = set(
        WishlistItem.objects.filter(wishlist__user=user, product__public_id__in=product_public_ids).values_list(
            "product__public_id", flat=True
        )
    )
    return {str(pid): pid in have for pid in product_public_ids}
