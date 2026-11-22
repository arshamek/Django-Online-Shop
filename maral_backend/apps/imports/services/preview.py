"""Read-only preview of a listing: what we received, what it would become, and what still blocks publication."""

from apps.product_sources.models import ExternalProduct, MatchStatus

from .canonical import _source_language  # noqa: F401
from .mapping import resolve_brand, resolve_category, resolve_option
from .readiness import publication_blockers


def build_preview(ext: ExternalProduct) -> dict:
    brand = resolve_brand(ext.source, ext.raw_brand)
    category = resolve_category(ext)
    variants, unmapped = [], []
    for ev in ext.variants.select_related("current_price", "current_availability", "canonical_variant").order_by("id"):
        for code, key, raw in (("size", ev.size_key, ev.size_raw), ("color", ev.color_key, ev.color_raw)):
            if raw and resolve_option(ext.source, code, key) is None:
                entry = {"attribute": code, "raw": raw}
                if entry not in unmapped:
                    unmapped.append(entry)
        price, avail = ev.current_price, ev.current_availability
        variants.append(
            {
                "source_variant_id": ev.external_variant_id,
                "sku": ev.sku,
                "size": ev.size_raw,
                "color": ev.color_raw,
                "gtin": ev.gtin,
                "gtin_trusted": ev.gtin_trusted,
                "is_active": ev.is_active,
                "price": None
                if price is None
                else {
                    "list": str(price.list_amount),
                    "sale": None if price.sale_amount is None else str(price.sale_amount),
                    "customer": None if price.customer_amount is None else str(price.customer_amount),
                    "currency": price.currency_code,
                    "observed_at": price.observed_at,
                },
                "availability": None if avail is None else {"status": avail.status, "quantity": avail.quantity_hint},
                "canonical_sku": ev.canonical_variant.sku if ev.canonical_variant else None,
            }
        )
    product = ext.canonical_product
    matched = ext.match_status in (MatchStatus.AUTO_MATCHED, MatchStatus.MANUALLY_MATCHED)
    return {
        "id": str(ext.public_id),
        "source": ext.source.code,
        "external_id": ext.external_id,
        "external_url": ext.external_url,
        "data_origin": ext.data_origin,
        "listing_status": ext.status,
        "pipeline_status": ext.pipeline_status,
        "pipeline_reason": ext.pipeline_reason,
        "error": {"code": ext.error_code, "message": ext.error_message} if ext.error_code else None,
        "match": {
            "status": ext.match_status,
            "confidence": None if ext.match_confidence is None else float(ext.match_confidence),
            "matched_by": ext.matched_by,
            "product": None
            if product is None
            else {"id": str(product.public_id), "slug": product.slug, "status": product.status},
            "candidates": [
                {
                    "product_id": str(c.product.public_id),
                    "slug": c.product.slug,
                    "score": float(c.score),
                    "signals": c.signals,
                    "status": c.status,
                }
                for c in ext.match_candidates.select_related("product")
            ],
        },
        "raw": {
            "title": ext.raw_title,
            "brand": ext.raw_brand,
            "category_id": ext.raw_category_id,
            "category_path": ext.raw_category_path,
            "description": ext.raw_description,
            "gender": ext.gender,
            "language": ext.language,
            "manufacturer_code": ext.manufacturer_code,
            "attributes": ext.raw_attributes,
        },
        "images": [
            {"position": i.position, "url": i.url, "width": i.width, "height": i.height} for i in ext.images.all()
        ],
        "variants": variants,
        "resolution": {
            "brand": brand.slug if brand else None,
            "category": category.slug if category else None,
            "unmapped_options": unmapped,
        },
        "publication_blockers": publication_blockers(ext) if matched else ["not_matched_to_a_canonical_product"],
        "first_seen_at": ext.first_seen_at,
        "last_seen_at": ext.last_seen_at,
        "last_imported_at": ext.last_imported_at,
    }
