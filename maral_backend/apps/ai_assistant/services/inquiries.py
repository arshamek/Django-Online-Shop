"""
Purchase inquiries for external products. An inquiry is a request for staff to look into a purchase; it creates no
order, quote, payment or reservation. Staff re-check price, availability and feasibility (three stamps) before an
inquiry may become FEASIBLE; an actual order still goes through import -> publish -> cart -> checkout.
"""

import uuid
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound

from apps.audit import services as audit
from apps.common.exceptions import BusinessRuleViolation, Conflict
from apps.product_sources.models import ExternalProduct

from ..constants import EXTERNAL_SOURCES
from ..models import AssistantConfig, InquiryStatus, PurchaseInquiry
from . import limits, pricing_estimate
from .safety import clean_text
from .sources import snapshots
from .sources.base import SearchSpec

OPEN = (InquiryStatus.NEW, InquiryStatus.UNDER_REVIEW)
MAX_OPEN_PER_USER = 20


def _find_product(ref: dict, config) -> ExternalProduct:
    if ref.get("source") not in EXTERNAL_SOURCES or ref["source"] not in config.enabled_sources:
        raise BusinessRuleViolation(
            "Purchase inquiries are only for Trendyol or Namshi products.", code="inquiry_source_unsupported"
        )
    try:
        pid = uuid.UUID(str(ref.get("id")))
    except (ValueError, TypeError):
        raise NotFound("Product not found.") from None
    qs = (
        snapshots.visible_listings(config, ref["source"])
        .select_related("source", "canonical_product__category")
        .filter(public_id=pid)
    )
    product = qs.first()
    if product is None:
        raise NotFound("Product not found.")
    return product


@transaction.atomic
def create(
    request, user, *, ref: dict, size: str, color: str, quantity: int, note: str, conversation=None
) -> PurchaseInquiry:
    config = AssistantConfig.load()
    product = _find_product(ref, config)
    variants = list(
        product.variants.filter(is_active=True).select_related(
            "current_price", "current_availability", "canonical_variant"
        )
    )
    sizes = {v.size_raw.lower(): v.size_raw for v in variants if v.size_raw}
    colors = {v.color_raw.lower(): v.color_raw for v in variants if v.color_raw}
    size, color = (size or "").strip(), (color or "").strip()
    if sizes and not size:
        raise BusinessRuleViolation("Choose a size.", code="size_required", fields={"size": ["Required."]})
    if colors and not color:
        raise BusinessRuleViolation("Choose a colour.", code="color_required", fields={"color": ["Required."]})
    if size and size.lower() not in sizes:
        raise BusinessRuleViolation(
            "That size is not offered for this product.", code="size_not_offered", fields={"size": ["Not offered."]}
        )
    if color and color.lower() not in colors:
        raise BusinessRuleViolation(
            "That colour is not offered for this product.", code="color_not_offered", fields={"color": ["Not offered."]}
        )
    size, color = sizes.get(size.lower(), ""), colors.get(color.lower(), "")
    if PurchaseInquiry.objects.filter(user=user, status__in=OPEN).count() >= MAX_OPEN_PER_USER:
        raise limits.UsageLimitExceeded("Too many open purchase inquiries.", code="inquiry_limit")
    if PurchaseInquiry.objects.filter(
        user=user, external_product=product, size=size, color=color, status__in=OPEN
    ).exists():
        raise Conflict("You already have an open inquiry for this product, size and colour.", code="inquiry_duplicate")

    spec = SearchSpec(sizes=[size.lower()] if size else [], colors=[color.lower()] if color else [])
    now = timezone.now()
    cand = snapshots.build_candidate(
        product, spec, config=config, rule_set=pricing_estimate.active_rule_set(), now=now, apply_filters=False
    )
    prices = cand.data["prices"] if cand else {}
    est = prices.get("estimated_maral") or {}
    orig = prices.get("original") or {}
    inquiry = PurchaseInquiry.objects.create(
        user=user,
        conversation=conversation,
        source=product.source,
        external_product=product,
        external_id=product.external_id,
        source_url=product.external_url or "",
        title=clean_text(product.raw_title, 500),
        brand=clean_text(product.raw_brand, 200),
        size=size,
        color=color,
        quantity=quantity,
        customer_note=clean_text(note, 500),
        original_amount=Decimal(orig["amount"]) if orig.get("amount") else None,
        original_currency=orig.get("currency") or "",
        estimated_price_irr=cand.price_irr if cand else None,
        estimated_price_status=est.get("status", ""),
        price_rule_version=est.get("rule_version"),
        snapshot_observed_at=((cand.data.get("snapshot") or {}).get("price_observed_at") if cand else None),
        availability_seen=(cand.data["availability"].get("last_known") or cand.data["availability"]["status"])
        if cand
        else "",
    )
    inquiry.refresh_from_db()  # normalise the stored snapshot values (Decimal / datetime) before they are serialised
    audit.record("ai.purchase_inquiry_created", request=request, actor=user, target=inquiry, source=product.source.code)
    return inquiry


def cancel(request, user, inquiry: PurchaseInquiry) -> PurchaseInquiry:
    if inquiry.status not in OPEN:
        raise BusinessRuleViolation("This inquiry can no longer be cancelled.", code="inquiry_not_cancellable")
    inquiry.status = InquiryStatus.CANCELLED
    inquiry.save(update_fields=["status", "updated_at"])
    audit.record("ai.purchase_inquiry_cancelled", request=request, actor=user, target=inquiry)
    return inquiry


TERMINAL = (InquiryStatus.NOT_FEASIBLE, InquiryStatus.CLOSED, InquiryStatus.CANCELLED)
ALLOWED = {
    InquiryStatus.NEW: {InquiryStatus.UNDER_REVIEW, InquiryStatus.NOT_FEASIBLE, InquiryStatus.CLOSED},
    InquiryStatus.UNDER_REVIEW: {InquiryStatus.FEASIBLE, InquiryStatus.NOT_FEASIBLE, InquiryStatus.CLOSED},
    InquiryStatus.FEASIBLE: {InquiryStatus.CLOSED, InquiryStatus.NOT_FEASIBLE},
}


@transaction.atomic
def staff_update(
    request,
    staff_user,
    inquiry: PurchaseInquiry,
    *,
    status=None,
    price_checked=None,
    availability_checked=None,
    feasibility_checked=None,
    staff_notes=None,
) -> PurchaseInquiry:
    inquiry = PurchaseInquiry.objects.select_for_update().get(pk=inquiry.pk)
    if inquiry.status in TERMINAL:
        raise BusinessRuleViolation("This inquiry is closed and can no longer be changed.", code="inquiry_closed")
    now = timezone.now()
    for flag, field in (
        (price_checked, "price_checked_at"),
        (availability_checked, "availability_checked_at"),
        (feasibility_checked, "feasibility_checked_at"),
    ):
        if flag:
            setattr(inquiry, field, now)
    if staff_notes is not None:
        inquiry.staff_notes = clean_text(staff_notes, 2000)
    if status and status != inquiry.status:
        if status not in ALLOWED.get(inquiry.status, set()):
            raise BusinessRuleViolation(
                f"Cannot move an inquiry from {inquiry.status} to {status}.", code="inquiry_transition_invalid"
            )
        if status == InquiryStatus.FEASIBLE and not (
            inquiry.price_checked_at and inquiry.availability_checked_at and inquiry.feasibility_checked_at
        ):
            raise BusinessRuleViolation(
                "Re-check price, availability and feasibility before marking an inquiry feasible.",
                code="inquiry_recheck_required",
            )
        inquiry.status = status
    inquiry.handled_by = staff_user
    inquiry.save()
    audit.record(
        "ai.purchase_inquiry_updated", request=request, actor=staff_user, target=inquiry, status=inquiry.status
    )
    return inquiry
