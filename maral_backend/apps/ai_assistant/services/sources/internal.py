"""
Maral's own catalogue. Reuses the Phase 4 search backend, filters, visibility rules and card data
(`catalog.selectors`, `catalog.presenters`); nothing here re-implements catalogue search.
"""

from decimal import Decimal

from apps.brands.models import Brand
from apps.catalog import presenters, selectors
from apps.catalog.context import CatalogContext
from apps.catalog.models import AvailabilityStatus
from apps.catalog.params import ProductParams
from apps.common.languages import language_chain
from apps.common.text import normalize_search_text

from ...constants import SOURCE_MARAL, Availability, PriceStatus
from .. import vocab
from ..safety import clean_text
from .base import Candidate, SearchSpec

MAP = {
    AvailabilityStatus.IN_STOCK: Availability.IN_STOCK,
    AvailabilityStatus.OUT_OF_STOCK: Availability.OUT_OF_STOCK,
    AvailabilityStatus.UNKNOWN: Availability.UNKNOWN,
}


def make_context(request, language: str) -> CatalogContext:
    return CatalogContext(request=request, lang=language, chain=tuple(language_chain(language)), currency="IRT")


def _queries(spec: SearchSpec) -> list[str]:
    """Search strings for the catalogue's AND-token backend: one per concept synonym, then the raw keywords."""
    brand_hint = [b.lower() for b in spec.brands if b.isascii() and not spec.brand_ids]
    out: list[str] = []
    if spec.concept:
        concept = vocab.CONCEPTS[spec.concept]
        for term in (*concept["fa"], *concept["latin"]):
            out.append(" ".join([*brand_hint, term, *spec.keywords]).strip())
    elif spec.keywords or brand_hint:
        out.append(" ".join([*brand_hint, *spec.keywords]).strip())
    else:
        out.append("")
    return list(dict.fromkeys(normalize_search_text(q) for q in out))


def _params(spec: SearchSpec, q: str, *, with_options: bool) -> ProductParams:
    brand_slugs = (
        list(Brand.objects.filter(id__in=spec.brand_ids).values_list("slug", flat=True)) if spec.brand_ids else []
    )
    params = ProductParams(
        brands=brand_slugs,
        colors=list(spec.colors) if with_options else [],
        sizes=list(spec.sizes) if with_options else [],
        price_min_irr=spec.min_irr,
        price_max_irr=spec.max_irr,
        q=q,
        sort="relevance" if q else "popular",
    )
    return params


def _variants(product, ctx) -> list:
    out = []
    for variant in product.active_variants[:30]:
        axes = {link.attribute.code: presenters.option_name(link.option, ctx) for link in variant.options.all()}
        codes = {link.attribute.code: link.option.code for link in variant.options.all()}
        avail = getattr(variant, "availability", None)
        out.append(
            {
                "variant_id": str(variant.public_id),
                "size": axes.get("size"),
                "size_code": codes.get("size"),
                "color": axes.get("color"),
                "color_code": codes.get("color"),
                "availability": MAP.get(avail.status, Availability.UNKNOWN) if avail else Availability.UNKNOWN,
                "verified_at": avail.observed_at.isoformat() if avail else None,
            }
        )
    return out


def _availability(product, variants: list) -> dict:
    statuses = {v["availability"] for v in variants}
    if Availability.IN_STOCK in statuses:
        status = Availability.IN_STOCK
    elif statuses == {Availability.OUT_OF_STOCK}:
        status = Availability.OUT_OF_STOCK
    else:
        status = Availability.UNKNOWN
    times = [getattr(v, "availability", None) for v in product.active_variants]
    observed = max((a.observed_at for a in times if a), default=None)
    direct = any(v.fulfillment_type == "DIRECT" for v in product.active_variants)
    return {
        "status": status,
        "last_known": None,
        "confidence": "high" if direct else "medium",
        "verified_at": observed.isoformat() if observed else None,
        "basis": "maral_inventory" if direct else "catalogue_synced_from_source",
        "note": None if direct else "recheck_at_checkout",
    }


def to_candidate(product, ctx: CatalogContext, language_codes: list) -> Candidate:
    card = presenters.build_card(product, ctx)
    translation = presenters.pick(list(product.translations.all()), ctx.chain, product.source_language)
    description = clean_text((translation.short_description or translation.description) if translation else "", 300)
    final = card["price"]["final"]
    variants = _variants(product, ctx)
    data = {
        "ref": {"source": SOURCE_MARAL, "id": card["id"]},
        "id": card["id"],
        "kind": "internal",
        "source": {"code": SOURCE_MARAL, "name": "Maral", "label": "maral"},
        "title": card["name"],
        "brand": (card["brand"] or {}).get("name"),
        "category": (card["category"] or {}).get("name"),
        "description": description or None,
        "images": [{"url": i["url"], "thumbnail_url": i["thumbnail_url"], "alt": i["alt"]} for i in card["images"]],
        "product_url": None,
        "maral": {
            "slug": card["slug"],
            "detail_api_path": f"/api/v1/products/{card['slug']}",
            "purchasable_in_cart": True,
        },
        "identifier": {"product_id": card["id"], "slug": card["slug"]},
        "prices": {
            "original": {
                "amount": card["price"]["original"]["amount"],
                "currency": card["price"]["original"]["currency"],
                "observed_at": None,
            },
            "estimated_maral": {
                "amount": final["amount"],
                "currency": "IRT",
                "status": PriceStatus.LISTED,
                "provisional": False,
                "missing_costs": [],
                "blockers": [],
                "warnings": [],
                "rule_version": None,
                "calculated_at": product.projection.updated_at.isoformat()
                if getattr(product.projection, "updated_at", None)
                else None,
                "note": "published_maral_price_final_price_is_confirmed_at_checkout",
            },
            "confirmed": None,
            "discount_percent": card["price"]["discount_percent"],
        },
        "availability": _availability(product, variants),
        "sizes": [{"code": s["code"], "name": s["name"], "available": s["available"]} for s in card["sizes"]],
        "colors": [{"code": c["code"], "name": c["name"], "available": c["available"]} for c in card["colors"]],
        "variants": variants,
        "last_verified_at": None,
        "snapshot": None,
        "warnings": [],
    }
    data["last_verified_at"] = data["availability"]["verified_at"]
    return Candidate(
        data=data,
        source=SOURCE_MARAL,
        kind="internal",
        pk=product.pk,
        price_irr=Decimal(str(product.projection.price_final_irr))
        if product.projection.price_final_irr is not None
        else None,
    )


def search(spec: SearchSpec, ctx: CatalogContext, limit: int) -> tuple[list, list]:
    """(candidates, relaxed_filters). Strict first; size/colour are relaxed (and reported) when nothing matches."""
    relaxed: list = []
    for with_options in (True, False):
        if not with_options and not (spec.colors or spec.sizes):
            break
        ordered: list = []
        for q in _queries(spec):
            params = _params(spec, q, with_options=with_options)
            ids = list(selectors.listing_queryset(params).values_list("pk", flat=True)[: limit * 2])
            ordered.extend(i for i in ids if i not in ordered)
            if len(ordered) >= limit * 2:
                break
        if ordered:
            if not with_options:
                relaxed = [name for name, vals in (("size", spec.sizes), ("color", spec.colors)) if vals]
            break
    else:
        ordered = []
    if not ordered:
        return [], relaxed
    ordered = ordered[: limit * 2]
    qs = selectors.with_card_data(selectors.visible_products().filter(pk__in=ordered), ctx)
    by_pk = {p.pk: p for p in qs}
    candidates = [to_candidate(by_pk[pk], ctx, []) for pk in ordered if pk in by_pk]
    return candidates, relaxed
