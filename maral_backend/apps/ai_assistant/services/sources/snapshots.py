"""
Imported marketplace snapshots (Trendyol / Namshi).

This is NOT a live marketplace search. It searches `ExternalProduct` rows that the Phase 5 import engine already
stored, and labels every result as a stored snapshot with its observation time and an availability confidence.
Availability is never IN_STOCK here: a stored observation is NOT_VERIFIED (or UNKNOWN when nothing is stored).

Live keyword search for these sources stays disabled (`live_search_status`) until verified API access or an
authorised licensed feed exists; no provider is called and nothing is scraped.
"""

from datetime import timedelta

from django.db.models import Prefetch, Q
from django.utils import timezone

from apps.product_sources.models import (
    DataOrigin,
    ExternalProduct,
    ExternalProductVariant,
    ListingStatus,
    MatchStatus,
    PipelineStatus,
    ProductSource,
)
from apps.product_sources.providers.authorization import authorization_problems

from ...constants import EXTERNAL_SOURCES, Availability, ProviderStatus
from .. import pricing_estimate, vocab
from ..safety import clean_text, sanitize_untrusted
from ..textnorm import fold
from .base import Candidate, SearchSpec, SourceReport

EXCLUDED_PIPELINE = (PipelineStatus.FAILED, PipelineStatus.ARCHIVED)
MATCHED = (MatchStatus.AUTO_MATCHED, MatchStatus.MANUALLY_MATCHED)
CANDIDATE_LIMIT = 60


def visible_listings(config, code: str | None = None):
    """
    THE visibility rule for stored marketplace listings, shared by discovery (`search`), by-reference loading
    (compare / availability / select) and purchase inquiries, so no path can bypass it with a client-supplied id:
    active listing, active source, source enabled by staff, pipeline not failed/archived, and fixture rows only
    when staff allow them.
    """
    enabled = [c for c in config.enabled_sources if c in EXTERNAL_SOURCES]
    if code is not None:
        enabled = [c for c in enabled if c == code]
    qs = ExternalProduct.objects.filter(
        status=ListingStatus.ACTIVE, source__is_active=True, source__code__in=enabled
    ).exclude(pipeline_status__in=EXCLUDED_PIPELINE)
    if not config.allow_fixture_snapshots:
        qs = qs.exclude(data_origin=DataOrigin.FIXTURE)
    return qs


def live_search_status(source: ProductSource | None) -> tuple[str, str]:
    """Why live keyword search is not used for this source. It never calls a provider."""
    if source is None:
        return ProviderStatus.NOT_CONFIGURED, "The source is not configured."
    problems = authorization_problems(source)
    if problems:
        return ProviderStatus.AUTHORIZATION_REQUIRED, "Live access is not authorised: " + "; ".join(problems) + "."
    return (
        ProviderStatus.UNSUPPORTED,
        "Live keyword search is disabled: the integration only lists the seller's own products or reads a feed "
        "(no verified marketplace search capability).",
    )


def _terms(spec: SearchSpec) -> list[list[str]]:
    """AND of OR-groups of lowercase substrings (matched against marketplace text)."""
    groups: list[list[str]] = []
    if spec.concept:
        concept = vocab.CONCEPTS[spec.concept]
        groups.append([t.lower() for t in concept["latin"]])
    for brand in spec.brands:
        if brand.isascii():
            groups.append([brand.lower()])
    for word in spec.keywords:
        if word.isascii() and len(word) > 2:
            groups.append([word.lower()])
    return groups


def _text_q(group: list[str]) -> Q:
    q = Q()
    for term in group:
        q |= (
            Q(raw_title__icontains=term)
            | Q(raw_brand__icontains=term)
            | Q(raw_category_path__icontains=term)
            | Q(brand_key__icontains=term)
        )
    return q


def _images(product, source, config) -> tuple[list, str | None]:
    """Marketplace image URLs are exposed only when the source's written terms allow it (`allows_image_copy`).
    No staff/config switch can override a source's licence restriction."""
    imgs = list(product.images.all())[:3]
    if not imgs:
        return [], None
    if not source.allows_image_copy:
        return [], "image_urls_withheld_source_terms"
    return [
        {"url": i.url, "thumbnail_url": i.url, "alt": clean_text(i.alt or product.raw_title, 120)} for i in imgs
    ], None


def _last_availability(variant):
    avail = variant.current_availability
    return avail


def _variant_rows(variants) -> list:
    rows = []
    for v in variants[:30]:
        avail = v.current_availability
        rows.append(
            {
                "variant_id": v.external_variant_id,
                "size": v.size_raw or None,
                "size_code": v.size_key or None,
                "color": v.color_raw or None,
                "color_code": v.color_key or None,
                "availability": Availability.NOT_VERIFIED if avail else Availability.UNKNOWN,
                "last_known": avail.status if avail else None,
                "observed_at": avail.observed_at.isoformat() if avail else None,
            }
        )
    return rows


def _availability(variants, now) -> dict:
    observations = [v.current_availability for v in variants if v.current_availability]
    if not observations:
        return {
            "status": Availability.UNKNOWN,
            "last_known": None,
            "confidence": "none",
            "verified_at": None,
            "basis": "no_stored_observation",
            "note": None,
        }
    statuses = {o.status for o in observations}
    last_known = (
        "in_stock" if "in_stock" in statuses else ("out_of_stock" if statuses == {"out_of_stock"} else "unknown")
    )
    newest = max(o.observed_at for o in observations)
    fresh = now - newest < timedelta(hours=24)
    return {
        "status": Availability.NOT_VERIFIED,
        "last_known": last_known,
        "confidence": "medium" if fresh else "low",
        "verified_at": None,
        "basis": "stored_snapshot_not_live",
        "observed_at": newest.isoformat(),
        "note": "not_verified_with_the_marketplace",
    }


def _pick_variant(variants, spec: SearchSpec):
    """Cheapest priced variant that matches the requested size/colour (None when nothing matches)."""

    def ok(v):
        size_ok = not spec.sizes or fold(v.size_raw) in spec.sizes or (v.size_key or "").lower() in spec.sizes
        color_ok = not spec.colors or (v.color_key or "").lower() in spec.colors or fold(v.color_raw) in spec.colors
        return size_ok and color_ok

    matching = [v for v in variants if ok(v)]
    return matching, [v for v in matching if v.current_price is not None]


def search(spec: SearchSpec, *, code: str, config, rule_set, limit: int, now=None) -> tuple[list, SourceReport]:
    now = now or timezone.now()
    source = ProductSource.objects.filter(code=code).first()
    live_status, live_detail = live_search_status(source)
    report = SourceReport(
        source=code,
        status=ProviderStatus.NOT_CONFIGURED,
        mode="imported_snapshot",
        live_search_status=live_status,
        snapshot_status=ProviderStatus.NOT_CONFIGURED,
    )
    if source is None or not source.is_active:
        report.detail = "The source is not configured or is inactive."
        return [], report
    groups = _terms(spec)
    report.searched = True
    report.snapshot_status = ProviderStatus.AVAILABLE
    report.status = live_status  # the source's LIVE capability; the stored-snapshot search is reported separately
    report.detail = "Stored import snapshots were searched (not a live marketplace search). " + live_detail
    if not groups:
        return [], report
    qs = visible_listings(config, code)
    for group in groups:
        qs = qs.filter(_text_q(group))
    variants_qs = ExternalProductVariant.objects.filter(is_active=True).select_related(
        "current_price", "current_availability", "canonical_variant"
    )
    qs = (
        qs.select_related("source", "canonical_product", "canonical_product__category")
        .prefetch_related(Prefetch("variants", queryset=variants_qs, to_attr="active_variants"), "images")
        .order_by("-last_seen_at", "-id")[:CANDIDATE_LIMIT]
    )

    out: list[Candidate] = []
    for product in qs:
        cand = build_candidate(product, spec, config=config, rule_set=rule_set, now=now, report=report)
        if cand is not None:
            out.append(cand)
        if len(out) >= limit * 2:
            break
    report.result_count = len(out)
    return out, report


def build_candidate(product, spec: SearchSpec, *, config, rule_set, now, report=None, apply_filters=True):
    """Public representation of one stored listing. With `apply_filters` False (compare / availability / select of an
    already-shown product) size, colour and budget never exclude it."""
    source = product.source
    code = source.code
    variants = (
        product.active_variants
        if hasattr(product, "active_variants")
        else list(
            product.variants.filter(is_active=True).select_related(
                "current_price", "current_availability", "canonical_variant"
            )
        )
    )
    if not variants:
        return None
    matching, priced = _pick_variant(variants, spec if apply_filters else SearchSpec())
    if apply_filters and (spec.sizes or spec.colors) and not matching:
        if report is not None:
            report.relaxed = sorted(
                set(report.relaxed) | {n for n, v in (("size", spec.sizes), ("color", spec.colors)) if v}
            )
        return None
    chosen = (
        min(priced, key=lambda v: v.current_price.list_amount) if priced else (matching[0] if matching else variants[0])
    )
    est = pricing_estimate.estimate(
        chosen, product, rule_set=rule_set, max_age_hours=config.price_max_age_hours, now=now
    )
    price_irr = pricing_estimate.to_decimal_irr(est)
    est.pop("price_irr", None)
    if apply_filters and (spec.min_irr is not None or spec.max_irr is not None):
        if price_irr is None:
            if report is not None:
                report.excluded_no_price += 1
            return None
        if (spec.max_irr is not None and price_irr > spec.max_irr) or (
            spec.min_irr is not None and price_irr < spec.min_irr
        ):
            return None
    title, flagged = sanitize_untrusted(product.raw_title, 200)
    description, desc_flagged = sanitize_untrusted(product.raw_description, 300)
    warnings = []
    if flagged or desc_flagged:
        warnings.append("untrusted_text_withheld")
    if flagged:
        title = "(title withheld)"
    if desc_flagged:
        description = ""
    if product.data_origin == DataOrigin.FIXTURE:
        warnings.append("fixture_data_not_real")
    images, image_note = _images(product, source, config)
    availability = _availability(variants, now)
    observed = max([v.current_price.observed_at for v in variants if v.current_price] + [now - timedelta(days=3650)])
    age_hours = round((now - observed).total_seconds() / 3600, 1) if priced else None
    data = {
        "ref": {"source": code, "id": str(product.public_id)},
        "id": str(product.public_id),
        "kind": "imported_snapshot",
        "source": {"code": code, "name": source.name, "label": code},
        "title": title,
        "brand": clean_text(product.raw_brand, 80) or None,
        "category": clean_text(product.raw_category_path, 120) or None,
        "description": description or None,
        "images": images,
        "images_note": image_note,
        "product_url": product.external_url if (config.expose_marketplace_urls and product.external_url) else None,
        "maral": {
            "slug": None,
            "detail_api_path": None,
            "purchasable_in_cart": bool(
                product.canonical_product_id and product.pipeline_status == PipelineStatus.PUBLISHED
            ),
        },
        "identifier": {
            "external_id": product.external_id,
            "gtin": next((v.gtin for v in variants if v.gtin and v.gtin_trusted), None),
        },
        "prices": {
            "original": est.get("original"),
            "estimated_maral": {k: v for k, v in est.items() if k not in ("original", "confirmed")},
            "confirmed": None,
        },
        "availability": availability,
        "sizes": sorted({v.size_raw for v in variants if v.size_raw}),
        "colors": sorted({v.color_raw for v in variants if v.color_raw}),
        "variants": _variant_rows(variants),
        "last_verified_at": None,
        "snapshot": {
            "is_snapshot": True,
            "live_marketplace_result": False,
            "data_origin": product.data_origin,
            "last_seen_at": product.last_seen_at.isoformat(),
            "price_observed_at": observed.isoformat() if priced else None,
            "price_age_hours": age_hours,
            "stale": bool(age_hours is not None and age_hours > config.price_max_age_hours),
        },
        "warnings": warnings,
    }
    return Candidate(
        data=data,
        source=code,
        kind="snapshot",
        pk=product.pk,
        price_irr=price_irr,
        canonical_product_id=product.canonical_product_id if product.match_status in MATCHED else None,
    )
