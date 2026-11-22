"""
One product through the import workflow (steps 4-12). Steps 1-3 (job, provider, fetch) belong to jobs.py.

    validate -> normalise -> detect duplicates / upsert listing -> match or create canonical product
    -> save variants + prices -> queue translation -> queue pricing -> eligible for publication
"""

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Product
from apps.common.exceptions import Conflict
from apps.product_sources.models import (
    CandidateStatus,
    ExternalBrandMapping,
    ExternalCategoryMapping,
    ExternalOptionMapping,
    ExternalProduct,
    MatchStatus,
    ProductMatchCandidate,
)
from apps.product_sources.models import (
    PipelineStatus as S,
)
from apps.product_sources.providers.dto import ProviderProduct

from . import canonical, listing, matching, readiness
from .canonical import NeedsMapping
from .mapping import resolve_brand, resolve_category
from .normalize import normalize_product
from .statuses import set_status
from .validation import validate_product


class ItemFailure(Exception):
    """A non-retryable problem with one product. The rest of the job continues."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class ItemResult:
    action: str  # created | updated | unchanged | skipped | delisted
    ext: ExternalProduct | None = None
    note: str = ""


def process_product(raw: ProviderProduct, *, source, data_origin: str, gtin_trusted: bool) -> ItemResult:
    p = normalize_product(raw)
    report = validate_product(p)
    if not report.ok:
        raise ItemFailure(report.errors[0].code, report.errors[0].message)
    now = timezone.now()
    with transaction.atomic():
        change = listing.upsert_listing(
            source, p, report.usable_variants, data_origin=data_origin, gtin_trusted=gtin_trusted, now=now
        )
        ext = change.ext
        if ext.match_status == MatchStatus.REJECTED:
            return ItemResult("skipped", ext, "rejected_by_staff")
        if not p.is_listed:
            listing.mark_delisted(ext, now)
            _refresh_canonical_availability(ext)
            if ext.pipeline_status not in (S.PUBLISHED, S.ARCHIVED):
                if ext.pipeline_status == S.DISCOVERED:
                    set_status(ext, S.PROCESSING, "delisted_at_source")
                set_status(ext, S.ARCHIVED, "delisted_at_source")
            return ItemResult("delisted", ext)
        settled = {
            S.IMPORTED,
            S.TRANSLATION_PENDING,
            S.PRICING_PENDING,
            S.READY_TO_PUBLISH,
            S.PUBLISHED,
            S.PENDING_REVIEW,
            S.ARCHIVED,
        }
        if not change.payload_changed and ext.pipeline_status in settled:
            return ItemResult("unchanged", ext)
        action = "created" if change.created else "updated"
        set_status(ext, S.PROCESSING, "importing")
        run_matching_and_finish(ext)
        return ItemResult(action, ext)


def _refresh_canonical_availability(ext: ExternalProduct) -> None:
    for ev in ext.variants.exclude(canonical_variant=None).select_related("canonical_variant"):
        canonical.refresh_variant_availability(ev.canonical_variant)


def _to_review(ext: ExternalProduct, reason: str, outcome=None) -> None:
    if outcome is not None:
        matching.persist_candidates(ext, outcome.candidates)
    if ext.match_status != MatchStatus.AUTO_MATCHED:
        ext.match_status = MatchStatus.NEEDS_REVIEW
        ext.save(update_fields=["match_status", "updated_at"])
    set_status(ext, S.PENDING_REVIEW, reason)


def run_matching_and_finish(ext: ExternalProduct) -> None:
    outcome = matching.match(ext)
    if outcome.decision == "needs_review":
        _to_review(ext, outcome.reason, outcome)
        return
    finish_import(ext, outcome)


def finish_import(ext: ExternalProduct, outcome: matching.MatchOutcome) -> None:
    """Steps 8-12 once matching has decided. Also called when staff approve a held listing."""
    if outcome.decision == "auto_linked":
        matching.apply_link(
            ext,
            outcome.product,
            status=MatchStatus.AUTO_MATCHED,
            matched_by=outcome.matched_by,
            confidence=outcome.confidence,
        )
        product = outcome.product
    elif outcome.decision == "kept":
        product = outcome.product
    else:
        brand = resolve_brand(ext.source, ext.raw_brand)
        category = resolve_category(ext)
        if category is None:
            _to_review(ext, "category_unresolved", outcome)
            return
        product = canonical.create_canonical_product(ext, brand, category)
        matching.apply_link(
            ext, product, status=MatchStatus.AUTO_MATCHED, matched_by="rule:new-canonical", confidence=1.0
        )
        matching.persist_candidates(ext, outcome.candidates)
    ext.refresh_from_db()
    canonical.ensure_source_translation(product, ext)
    try:
        canonical.sync_canonical_variants(ext)
    except NeedsMapping as exc:
        set_status(ext, S.PENDING_REVIEW, f"option_unmapped:{exc.kind}:{exc.raw}"[:200])
        return
    set_status(ext, S.IMPORTED, "")
    readiness.refresh_readiness(ext, queue=True)


# ---------------------------------------------------------------------------- staff decisions
@transaction.atomic
def approve(
    ext: ExternalProduct,
    actor,
    *,
    product: Product | None = None,
    create_new: bool = False,
    brand=None,
    category=None,
    option_mappings: list[dict] | None = None,
) -> ExternalProduct:
    """Resolve a listing held in PENDING_REVIEW: optionally teach the importer a mapping, then either link it
    to a chosen canonical product or let it create a new one."""
    ext = ExternalProduct.objects.select_for_update().get(pk=ext.pk)
    if ext.pipeline_status != S.PENDING_REVIEW:
        raise Conflict("Only listings pending review can be approved.", code="not_pending_review")
    if product is not None and create_new:
        raise Conflict("Choose an existing product or create a new one, not both.", code="ambiguous_decision")
    if brand is not None:
        ExternalBrandMapping.objects.update_or_create(
            source=ext.source, raw_key=ext.brand_key, defaults={"brand": brand}
        )
    if category is not None:
        key = ext.raw_category_id or ext.raw_category_path
        from apps.common.text import normalize_search_text

        key = key if ext.raw_category_id else normalize_search_text(key)[:200]
        ExternalCategoryMapping.objects.update_or_create(
            source=ext.source, raw_key=key, defaults={"category": category}
        )
    for m in option_mappings or []:
        ExternalOptionMapping.objects.update_or_create(
            source=ext.source, attribute_code=m["attribute"], raw_key=m["raw"], defaults={"option": m["option"]}
        )
    now = timezone.now()
    ext.reviewed_by, ext.reviewed_at = actor, now
    ext.save(update_fields=["reviewed_by", "reviewed_at", "updated_at"])
    label = f"staff:{getattr(actor, 'public_id', 'unknown')}"
    set_status(ext, S.PROCESSING, "approved")
    if product is not None:
        matching.apply_link(ext, product, status=MatchStatus.MANUALLY_MATCHED, matched_by=label, confidence=1.0)
        ProductMatchCandidate.objects.filter(external_product=ext, product=product).update(
            status=CandidateStatus.APPROVED, reviewed_by=actor, reviewed_at=now
        )
        ProductMatchCandidate.objects.filter(external_product=ext, status=CandidateStatus.PENDING).update(
            status=CandidateStatus.REJECTED, reviewed_by=actor, reviewed_at=now
        )
        outcome = matching.MatchOutcome("kept", product, 1.0, label)
    elif ext.canonical_product_id:  # held for a missing option mapping: already linked
        outcome = matching.MatchOutcome("kept", ext.canonical_product, float(ext.match_confidence or 1), ext.matched_by)
    elif create_new:
        outcome = matching.MatchOutcome("new")
        ProductMatchCandidate.objects.filter(external_product=ext, status=CandidateStatus.PENDING).update(
            status=CandidateStatus.REJECTED, reviewed_by=actor, reviewed_at=now
        )
    else:
        outcome = matching.match(ext)
        if outcome.decision == "needs_review":
            _to_review(ext, outcome.reason, outcome)
            return ext
    ext.match_status = MatchStatus.MANUALLY_MATCHED if (product is not None or create_new) else ext.match_status
    ext.save(update_fields=["match_status", "updated_at"])
    if resolve_brand(ext.source, ext.raw_brand) is None and outcome.decision != "kept":
        set_status(ext, S.PENDING_REVIEW, "brand_unresolved")
        return ext
    finish_import(ext, outcome)
    if create_new and ext.canonical_product_id:  # a staff decision outranks the automatic rule label
        ext.match_status, ext.matched_by = MatchStatus.MANUALLY_MATCHED, label[:60]
        ext.save(update_fields=["match_status", "matched_by", "updated_at"])
    return ext


@transaction.atomic
def reject(ext: ExternalProduct, actor, reason: str = "") -> ExternalProduct:
    ext = ExternalProduct.objects.select_for_update().get(pk=ext.pk)
    if ext.pipeline_status == S.PUBLISHED:
        raise Conflict("A published product must be archived through the catalogue.", code="already_published")
    if ext.pipeline_status == S.ARCHIVED and ext.match_status == MatchStatus.REJECTED:
        return ext
    ext.match_status = MatchStatus.REJECTED
    ext.reviewed_by, ext.reviewed_at = actor, timezone.now()
    ext.save(update_fields=["match_status", "reviewed_by", "reviewed_at", "updated_at"])
    ProductMatchCandidate.objects.filter(external_product=ext, status=CandidateStatus.PENDING).update(
        status=CandidateStatus.REJECTED, reviewed_by=actor, reviewed_at=timezone.now()
    )
    if ext.pipeline_status == S.DISCOVERED:
        set_status(ext, S.PROCESSING, "rejected")
    set_status(ext, S.ARCHIVED, f"rejected:{reason}"[:200])
    return ext
