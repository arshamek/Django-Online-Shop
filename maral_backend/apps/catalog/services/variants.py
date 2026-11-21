import hashlib

from ..models import ProductVariant, ProductVariantOption


def compute_signature(option_ids) -> str:
    return hashlib.sha256(",".join(str(i) for i in sorted(option_ids)).encode()).hexdigest()


def sync_option_signature(variant_id: int) -> None:
    """Variant identity inside a product = hash of its sorted option ids (unique per product)."""
    option_ids = list(ProductVariantOption.objects.filter(variant_id=variant_id).values_list("option_id", flat=True))
    signature = compute_signature(option_ids) if option_ids else ""
    ProductVariant.objects.filter(pk=variant_id).exclude(option_signature=signature).update(option_signature=signature)


# --------------------------------------------------------------------------- staff write operations
from django.db import transaction  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.common.exceptions import Conflict, DomainError  # noqa: E402
from apps.product_sources.models import ProductSource  # noqa: E402

from ..models import (  # noqa: E402
    AttributeOption,
    Product,
    VariantAvailability,
    VariantPrice,
)


def _option(attribute_code: str, code: str) -> AttributeOption:
    option = (
        AttributeOption.objects.filter(attribute__code=attribute_code, code=code.strip().lower())
        .select_related("attribute")
        .first()
    )
    if option is None:
        raise DomainError(
            f"Unknown {attribute_code} '{code}'.", code="validation_error", fields={attribute_code: ["Unknown option."]}
        )
    return option


def _source(code: str) -> ProductSource:
    source = ProductSource.objects.filter(code=code).first()
    if source is None:
        raise DomainError("Unknown source.", code="validation_error", fields={"source": ["Unknown source."]})
    return source


def _check_fulfillment(fulfillment_type: str, source: ProductSource) -> None:
    probe = ProductVariant(fulfillment_type=fulfillment_type, source=source)
    from django.core.exceptions import ValidationError

    try:
        probe.clean()
    except ValidationError as exc:
        raise DomainError(
            "Fulfillment type and source do not match.", code="validation_error", fields=exc.message_dict
        ) from exc


def set_price(variant: ProductVariant, data: dict) -> VariantPrice:
    price, _ = VariantPrice.objects.update_or_create(
        variant=variant, defaults={**data, "computed_at": timezone.now(), "origin": "manual"}
    )
    return price


def set_availability(variant: ProductVariant, status: str) -> VariantAvailability:
    obj, _ = VariantAvailability.objects.update_or_create(
        variant=variant, defaults={"status": status, "observed_at": timezone.now(), "origin": "manual"}
    )
    return obj


@transaction.atomic
def create_variant(product: Product, data: dict) -> ProductVariant:
    source = _source(data["source"])
    _check_fulfillment(data["fulfillment_type"], source)
    options = []
    for axis in ("size", "color"):
        if data.get(axis):
            options.append(_option(axis, data[axis]))
    signature = compute_signature([o.pk for o in options]) if options else ""
    if signature and ProductVariant.objects.filter(product=product, option_signature=signature).exists():
        raise Conflict("A variant with these options already exists.", code="variant_exists")
    if ProductVariant.objects.filter(sku=data["sku"]).exists():
        raise Conflict("SKU already in use.", code="sku_exists", fields={"sku": ["Already in use."]})
    variant = ProductVariant.objects.create(
        product=product,
        sku=data["sku"],
        fulfillment_type=data["fulfillment_type"],
        source=source,
        source_product_url=data.get("source_product_url", ""),
        source_sku=data.get("source_sku", ""),
        is_active=data.get("is_active", True),
        sort_order=product.variants.count(),
    )
    for option in options:
        ProductVariantOption.objects.create(variant=variant, attribute=option.attribute, option=option)
    if data.get("price"):
        set_price(variant, dict(data["price"]))
    if data.get("availability"):
        set_availability(variant, data["availability"])
    return variant


@transaction.atomic
def update_variant(variant: ProductVariant, data: dict) -> ProductVariant:
    if "source" in data or "fulfillment_type" in data:
        source = _source(data["source"]) if "source" in data else variant.source
        fulfillment_type = data.get("fulfillment_type", variant.fulfillment_type)
        _check_fulfillment(fulfillment_type, source)
        variant.source, variant.fulfillment_type = source, fulfillment_type
    for field in ("is_active", "source_product_url", "source_sku"):
        if field in data:
            setattr(variant, field, data[field])
    variant.save()
    if data.get("price"):
        set_price(variant, dict(data["price"]))
    if data.get("availability"):
        set_availability(variant, data["availability"])
    return variant
