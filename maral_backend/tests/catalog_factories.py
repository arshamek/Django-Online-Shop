"""Small, explicit builders for catalogue tests (the 16-product demo set lives in apps/catalog/demo.py)."""

import io
import itertools
from decimal import Decimal

from PIL import Image

from apps.brands.models import Brand
from apps.catalog.models import (
    AttributeOption,
    AttributeOptionTranslation,
    Product,
    ProductAttribute,
    ProductStatus,
    ProductTranslation,
    ProductVariant,
    ProductVariantOption,
    VariantAvailability,
    VariantPrice,
)
from apps.catalog.services import projection
from apps.categories.models import Category, CategoryTranslation
from apps.product_sources.models import IntegrationType, ProductSource

_counter = itertools.count(1)


def source(code="trendyol", **extra):
    defaults = {
        "name": code.title(),
        "country_code": "TR",
        "base_currency": "TRY",
        "integration_type": IntegrationType.INTERNAL if code == "maral" else IntegrationType.MANUAL,
    }
    obj, _ = ProductSource.objects.get_or_create(code=code, defaults={**defaults, **extra})
    return obj


def brand(name="Acme", slug=None, **extra):
    obj, _ = Brand.objects.get_or_create(slug=slug or name.lower().replace(" ", "-"), defaults={"name": name, **extra})
    return obj


def category(slug="women", parent=None, fa=None, en=None, **extra):
    obj, created = Category.objects.get_or_create(slug=slug, defaults={"parent": parent, **extra})
    if created:
        CategoryTranslation.objects.create(category=obj, language="fa", name=fa or slug)
        if en:
            CategoryTranslation.objects.create(category=obj, language="en", name=en)
    return obj


def axes():
    size, _ = ProductAttribute.objects.get_or_create(code="size", defaults={"is_variant_axis": True})
    color, _ = ProductAttribute.objects.get_or_create(code="color", defaults={"is_variant_axis": True})
    return size, color


def option(attribute_code, code, name=None, swatch=""):
    attribute = axes()[0 if attribute_code == "size" else 1]
    obj, created = AttributeOption.objects.get_or_create(
        attribute=attribute, code=code.lower(), defaults={"swatch": swatch}
    )
    if created:
        AttributeOptionTranslation.objects.create(option=obj, language="fa", name=name or code.upper())
    return obj


def product(
    slug=None,
    *,
    names=None,
    brand_obj=None,
    category_obj=None,
    status=ProductStatus.ACTIVE,
    variants=None,
    popularity=0.0,
    new_until=None,
    **extra,
):
    n = next(_counter)
    slug = slug or f"product-{n}"
    brand_obj = brand_obj or brand()
    category_obj = category_obj or category()
    obj = Product.objects.create(
        slug=slug,
        brand=brand_obj,
        category=category_obj,
        status=status,
        popularity_score=popularity,
        new_until=new_until,
        **extra,
    )
    for language, name in (names or {"fa": f"محصول {n}"}).items():
        ProductTranslation.objects.create(product=obj, language=language, name=name)
    for spec in variants if variants is not None else [{}]:
        add_variant(obj, **spec)
    projection.refresh_projection(obj.pk)
    return obj


def add_variant(
    obj,
    *,
    size=None,
    color=None,
    fulfillment="PROXY_PURCHASE",
    source_code="trendyol",
    final=1_000_000,
    list_price=None,
    discount=0,
    original=100,
    currency="TRY",
    available=True,
    price=True,
    active=True,
    sku=None,
):
    sku = sku or f"SKU-{next(_counter)}"
    variant = ProductVariant.objects.create(
        product=obj, sku=sku, fulfillment_type=fulfillment, source=source(source_code), is_active=active
    )
    if size:
        ProductVariantOption.objects.create(
            variant=variant, attribute=axes()[0], option=option("size", size, size.upper())
        )
    if color:
        ProductVariantOption.objects.create(variant=variant, attribute=axes()[1], option=option("color", color, color))
    if price:
        list_irr = Decimal((list_price or final) * 10)
        VariantPrice.objects.create(
            variant=variant,
            original_amount=Decimal(original),
            original_currency=currency,
            list_irr=list_irr,
            final_irr=Decimal(final * 10),
            discount_percent=Decimal(discount),
        )
    VariantAvailability.objects.create(variant=variant, status="in_stock" if available else "out_of_stock")
    return variant


def image_bytes(fmt="JPEG", size=(400, 500), color="#885533"):
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, fmt)
    return buffer.getvalue()
