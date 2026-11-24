"""Cart response. Everything here is computed on the server from the catalogue; the client sends no prices."""

from decimal import Decimal

from django.db.models import Prefetch

from apps.catalog.context import money
from apps.catalog.models import (
    AttributeOptionTranslation,
    FulfillmentType,
    ProductImage,
    ProductTranslation,
    ProductVariantOption,
)
from apps.catalog.presenters import file_url, pick
from apps.shipping.services import methods as shipping

from ..models import Cart
from . import availability


def _variant_opts(variant, ctx):
    out = {}
    for vo in variant.options.all():
        code = vo.attribute.code
        translation = pick(list(vo.option.translations.all()), ctx.chain)
        out[code] = {
            "code": vo.option.code,
            "name": translation.name if translation else vo.option.code,
            **({"swatch": vo.option.swatch} if vo.option.swatch else {}),
        }
    return out


def _lines(cart: Cart, ctx):
    items = list(
        cart.items.select_related(
            "variant__product__brand", "variant__product__category", "variant__price", "variant__availability", "variant__source"
        ).prefetch_related(
            Prefetch(
                "variant__options",
                queryset=ProductVariantOption.objects.select_related("attribute", "option").prefetch_related(
                    Prefetch("option__translations", queryset=AttributeOptionTranslation.objects.all())
                ),
            ),
            Prefetch("variant__product__translations", queryset=ProductTranslation.objects.all()),
        )
    )
    product_ids = {i.variant.product_id for i in items}
    first_image = {}
    for image in ProductImage.objects.published().filter(product_id__in=product_ids).order_by("sort_order", "id"):
        first_image.setdefault(image.product_id, image)
    return items, first_image


def build_cart(cart: Cart, ctx, *, guest_token: str | None = None) -> dict:
    items, first_image = _lines(cart, ctx)
    variants = {i.variant_id: i.variant for i in items}
    issues = availability.assess([(i.variant_id, i.quantity) for i in items], variants)
    cur = ctx.currency
    lines, subtotal, list_total, blocking = [], Decimal(0), Decimal(0), False
    count = 0
    for item in items:
        v = item.variant
        price = getattr(v, "price", None)
        line_issues = issues[v.pk]
        blocking = blocking or bool(line_issues)
        translation = pick(list(v.product.translations.all()), ctx.chain, v.product.source_language)
        name = translation.name if translation else v.product.slug
        image = first_image.get(v.product_id)
        opts = _variant_opts(v, ctx)
        row = {
            "line_id": str(item.public_id),
            "variant_id": str(v.public_id),
            "sku": v.sku,
            "product": {
                "id": str(v.product.public_id),
                "slug": v.product.slug,
                "name": name,
                "brand": v.product.brand.name,
                "image": file_url(ctx, image.image) if image else None,
            },
            "size": opts.get("size"),
            "color": opts.get("color"),
            "quantity": item.quantity,
            "fulfillment": "stock" if v.fulfillment_type == FulfillmentType.DIRECT_INVENTORY else "order",
            "fulfillment_type": v.fulfillment_type,
            "source": v.source.code,
            "available": not line_issues,
            "issues": [i.as_dict() for i in line_issues],
        }
        if price is not None:
            line_final, line_list = price.final_irr * item.quantity, price.list_irr * item.quantity
            subtotal += line_final
            list_total += line_list
            row.update(
                unit_price=money(price.final_irr, cur),
                list_unit_price=money(price.list_irr, cur),
                line_total=money(line_final, cur),
                discount_percent=float(price.discount_percent),
            )
        else:
            row.update(unit_price=None, list_unit_price=None, line_total=None, discount_percent=0)
        count += item.quantity
        lines.append(row)

    warnings: list[str] = []
    estimate = shipping.estimate(subtotal) if items else None
    if items and estimate is None:
        warnings.append("shipping_not_configured")
    total = subtotal + (estimate.fee_irr if estimate else Decimal(0))
    body = {
        "id": str(cart.public_id),
        "version": cart.version,
        "currency": cur,
        "items": lines,
        "item_count": count,
        "totals": {
            "subtotal": money(subtotal, cur),
            "list_total": money(list_total, cur),
            "discount": money(max(list_total - subtotal, Decimal(0)), cur),
            "shipping_estimate": None
            if estimate is None
            else {
                "method": estimate.code,
                "amount": money(estimate.fee_irr, cur),
                "is_free": estimate.is_free,
                "status": estimate.value_status,
            },
            "total": money(total, cur),
            "total_is_partial": bool(items) and estimate is None,
        },
        "price_basis": "storefront_estimate",
        "checkout_ready": bool(items) and not blocking,
        "warnings": warnings,
    }
    if guest_token:
        body["guest_token"] = guest_token
    return body
