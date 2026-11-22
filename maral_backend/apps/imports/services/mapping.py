"""
Vocabulary resolution: source words -> our canonical brand / category / options. Resolution is exact and
unambiguous or it returns None; the importer never guesses (an unresolved word goes to staff review).
"""

from django.utils.text import slugify

from apps.brands.models import Brand, BrandAlias
from apps.catalog.models import AttributeOption
from apps.categories.models import Category
from apps.common.text import normalize_search_text
from apps.product_sources.models import (
    ExternalBrandMapping,
    ExternalCategoryMapping,
    ExternalOptionMapping,
    ExternalProduct,
)


def brand_key(raw_brand: str) -> str:
    return normalize_search_text(raw_brand)[:200]


def resolve_brand(source, raw_brand: str) -> Brand | None:
    key = brand_key(raw_brand)
    if not key:
        return None
    mapped = ExternalBrandMapping.objects.filter(source=source, raw_key=key).select_related("brand").first()
    if mapped:
        return mapped.brand
    alias = BrandAlias.objects.filter(alias_normalised=key).select_related("brand").first()
    if alias:
        return alias.brand
    matches = list(Brand.objects.filter(name__iexact=raw_brand.strip())[:2])
    if len(matches) != 1:
        matches = list(Brand.objects.filter(slug=slugify(raw_brand))[:2])
    return matches[0] if len(matches) == 1 else None


def category_keys(ext: ExternalProduct) -> list[str]:
    keys = [ext.raw_category_id] if ext.raw_category_id else []
    path = normalize_search_text(ext.raw_category_path)
    if path:
        keys.append(path[:200])
    return keys


def resolve_category(ext: ExternalProduct) -> Category | None:
    for key in category_keys(ext):
        mapped = (
            ExternalCategoryMapping.objects.filter(source=ext.source, raw_key=key).select_related("category").first()
        )
        if mapped:
            return mapped.category
    return None


def resolve_option(source, attribute_code: str, raw_key: str) -> AttributeOption | None:
    if not raw_key:
        return None
    mapped = (
        ExternalOptionMapping.objects.filter(source=source, attribute_code=attribute_code, raw_key=raw_key)
        .select_related("option")
        .first()
    )
    if mapped:
        return mapped.option
    return AttributeOption.objects.filter(attribute__code=attribute_code, code=raw_key.strip().lower()).first()
