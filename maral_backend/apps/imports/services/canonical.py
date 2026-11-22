"""Steps 8-9: match-or-create the canonical product, then save its variants (with source-observed availability)."""

import hashlib
import re

from django.utils.text import slugify

from apps.catalog.models import (
    RESERVED_SLUGS,
    FulfillmentType,
    Product,
    ProductStatus,
    ProductTranslation,
    ProductVariant,
    ProductVariantOption,
    TranslationOrigin,
    TranslationStatus,
    VariantAvailability,
)
from apps.catalog.services.variants import compute_signature
from apps.common.languages import SUPPORTED_LANGUAGES
from apps.product_sources.models import ExternalProduct, ExternalProductVariant

from .mapping import resolve_option


class NeedsMapping(Exception):
    """A source word has no canonical equivalent yet; staff must map it. Not a failure."""

    def __init__(self, kind: str, raw: str):
        super().__init__(f"{kind}:{raw}")
        self.kind, self.raw = kind, raw


def _unique_slug(brand, title: str, ext: ExternalProduct) -> str:
    base = slugify(f"{brand.slug} {title}", allow_unicode=False)[:120].strip("-") or brand.slug
    slug = f"{base}-{ext.public_id.hex[:6]}"
    if slug in RESERVED_SLUGS or Product.objects.filter(slug=slug).exists():
        slug = f"{base}-{ext.public_id.hex[:12]}"
    return slug[:160]


def _source_language(ext: ExternalProduct) -> str:
    return ext.language if ext.language in SUPPORTED_LANGUAGES else "en"


def create_canonical_product(ext: ExternalProduct, brand, category) -> Product:
    product = Product.objects.create(
        slug=_unique_slug(brand, ext.raw_title, ext),
        brand=brand,
        category=category,
        status=ProductStatus.DRAFT,
        source_language=_source_language(ext),
        manufacturer_model_code=ext.manufacturer_code[:80],
    )
    ext.is_primary_listing = True
    ext.save(update_fields=["is_primary_listing", "updated_at"])
    ensure_source_translation(product, ext)
    return product


def ensure_source_translation(product: Product, ext: ExternalProduct) -> None:
    """The source text is kept as an APPROVED SOURCE-origin row; staff or the translation phase add the rest.
    Human edits are never overwritten, and machine translations go stale when the source text changes."""
    language = product.source_language
    row = ProductTranslation.objects.filter(product=product, language=language).first()
    if row is None:
        ProductTranslation.objects.create(
            product=product,
            language=language,
            name=ext.raw_title[:255],
            short_description=ext.raw_description[:400],
            description=ext.raw_description,
            origin=TranslationOrigin.SOURCE,
            status=TranslationStatus.APPROVED,
            source_text_hash=ext.content_hash,
            provider=f"import:{ext.source.code}",
        )
        return
    if ext.is_primary_listing and row.origin == TranslationOrigin.SOURCE and row.source_text_hash != ext.content_hash:
        row.name, row.description = ext.raw_title[:255], ext.raw_description
        row.short_description, row.source_text_hash = ext.raw_description[:400], ext.content_hash
        row.save()
        ProductTranslation.objects.filter(product=product, origin=TranslationOrigin.MACHINE).exclude(
            language=language
        ).update(status=TranslationStatus.STALE)


def _variant_sku(ext: ExternalProduct, ev: ExternalProductVariant) -> str:
    base = re.sub(r"[^A-Z0-9_-]", "-", f"{ext.source.code}-{ext.external_id}-{ev.external_variant_id}".upper())[:70]
    sku = base
    if ProductVariant.objects.filter(sku=sku).exists():
        sku = f"{base[:60]}-{hashlib.sha1(base.encode()).hexdigest()[:8].upper()}"
    return sku


def sync_canonical_variants(ext: ExternalProduct) -> list[ProductVariant]:
    """Link or create one canonical variant per priced, active source variant. Variant identity inside a
    product is its option signature; a second source offering the same size/colour links to the same variant."""
    product = ext.canonical_product
    result: list[ProductVariant] = []
    plan = []
    for ev in ext.variants.filter(is_active=True, current_price__isnull=False).select_related("canonical_variant"):
        options = []
        for code, raw_key, raw in (("size", ev.size_key, ev.size_raw), ("color", ev.color_key, ev.color_raw)):
            if not raw:
                continue
            option = resolve_option(ext.source, code, raw_key)
            if option is None:
                raise NeedsMapping(code, raw)
            options.append(option)
        plan.append((ev, options))
    for ev, options in plan:
        variant = ev.canonical_variant
        signature = compute_signature([o.pk for o in options]) if options else ""
        if variant is None and signature:
            variant = ProductVariant.objects.filter(product=product, option_signature=signature).first()
        if variant is None:
            variant = ProductVariant.objects.create(
                product=product,
                sku=_variant_sku(ext, ev),
                fulfillment_type=FulfillmentType.PROXY_PURCHASE,
                source=ext.source,
                source_product_url=(ev.source_url or ext.external_url)[:500],
                source_sku=(ev.sku or ev.external_variant_id)[:120],
                sort_order=product.variants.count(),
            )
            for option in options:
                ProductVariantOption.objects.create(variant=variant, attribute=option.attribute, option=option)
        if ev.canonical_variant_id != variant.pk:
            ev.canonical_variant = variant
            ev.save(update_fields=["canonical_variant"])
        result.append(variant)
    for variant in result:
        refresh_variant_availability(variant)
    return result


def refresh_variant_availability(variant: ProductVariant) -> None:
    """Best observation across every active source variant behind this canonical variant."""
    statuses = [
        ev.current_availability.status
        for ev in variant.external_variants.filter(is_active=True).select_related("current_availability")
        if ev.current_availability_id
    ]
    if "in_stock" in statuses:
        status = "in_stock"
    elif statuses and all(s == "out_of_stock" for s in statuses):
        status = "out_of_stock"
    elif not statuses and not variant.external_variants.filter(is_active=True).exists():
        status = "out_of_stock"
    else:
        status = "unknown"
    from django.utils import timezone

    VariantAvailability.objects.update_or_create(
        variant=variant, defaults={"status": status, "observed_at": timezone.now(), "origin": "import"}
    )
