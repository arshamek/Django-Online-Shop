"""One place that decides whether a variant can be bought, in what quantity, right now."""

from dataclasses import dataclass

from django.conf import settings

from apps.catalog.models import AvailabilityStatus, FulfillmentType, Product, ProductVariant
from apps.inventory.services import stock


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    available_quantity: int | None = None

    def as_dict(self):
        out = {"code": self.code, "message": self.message}
        if self.available_quantity is not None:
            out["available_quantity"] = self.available_quantity
        return out


def max_quantity() -> int:
    return settings.CART_MAX_QUANTITY_PER_LINE


def load_variants(variant_ids):
    return {
        v.pk: v
        for v in ProductVariant.objects.filter(pk__in=list(variant_ids)).select_related(
            "product", "product__brand", "product__category", "price", "availability", "source"
        )
    }


def assess(lines, variants=None) -> dict[int, list[Issue]]:
    """
    `lines` = [(variant_id, quantity)] -> {variant_id: [Issue]} (empty list = purchasable as asked).
    Direct variants are judged by internal stock, proxy variants by the last observed source availability.
    """
    variants = variants if variants is not None else load_variants(v for v, _ in lines)
    visible = set(Product.objects.visible().filter(pk__in={v.product_id for v in variants.values()}).values_list("pk", flat=True))
    direct_ids = [vid for vid, v in variants.items() if v.fulfillment_type == FulfillmentType.DIRECT_INVENTORY]
    sellable = stock.sellable_quantities(direct_ids) if direct_ids else {}
    out: dict[int, list[Issue]] = {}
    for variant_id, quantity in lines:
        variant = variants.get(variant_id)
        issues: list[Issue] = []
        if variant is None or not variant.is_active or variant.product_id not in visible:
            issues.append(Issue("unavailable", "This product is no longer available."))
        elif getattr(variant, "price", None) is None:
            issues.append(Issue("price_unavailable", "A price is not available for this product right now."))
        elif variant.fulfillment_type == FulfillmentType.DIRECT_INVENTORY:
            have = sellable.get(variant_id, 0)
            if have <= 0:
                issues.append(Issue("out_of_stock", "Out of stock.", 0))
            elif quantity > have:
                issues.append(Issue("insufficient_stock", f"Only {have} left.", have))
        else:
            availability = getattr(variant, "availability", None)
            if availability is None or availability.status != AvailabilityStatus.IN_STOCK:
                issues.append(Issue("out_of_stock", "Currently unavailable from the source store."))
        if quantity > max_quantity():
            issues.append(Issue("max_quantity_exceeded", f"At most {max_quantity()} per item.", max_quantity()))
        out[variant_id] = issues
    return out
