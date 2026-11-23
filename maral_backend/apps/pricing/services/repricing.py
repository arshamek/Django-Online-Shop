"""
Keeps VariantPrice (what the storefront shows) in step with source prices, rates and rules.

Non-destructive: if a price cannot be calculated the existing price is left alone and the reason is recorded in
PriceHistory (outcome `blocked`). Manual prices are never overwritten unless `force`. Concurrent writers are
serialised by a row lock on the variant and, as a backstop, the unique (variant, sequence) constraint.
"""

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

import structlog
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.catalog.models import PriceOrigin, ProductVariant, VariantPrice
from apps.common.exceptions import Conflict

from ..errors import PriceUnavailable
from ..models import PriceCause, PriceHistory, PriceOutcome
from . import inputs
from .engine import calculate
from .presentation import customer_breakdown
from .rulesets import active_ruleset

logger = structlog.get_logger("maral.pricing")


@dataclass
class RepriceResult:
    outcome: str  # applied | unchanged | blocked | skipped
    reason: str = ""
    final_irr: Decimal | None = None
    history_id: int | None = None


def _hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def reprice_variant(
    variant_id: int,
    *,
    cause: str = PriceCause.MANUAL,
    actor=None,
    force: bool = False,
    now=None,
    rule_set=None,
    retries: int = 2,
) -> RepriceResult:
    for attempt in range(retries + 1):
        try:
            return _reprice_once(variant_id, cause, actor, force, now, rule_set)
        except IntegrityError:  # a concurrent writer took the same history sequence: re-read and recalculate
            if attempt == retries:
                raise Conflict(
                    "The price was changed by another process; try again.", code="price_update_conflict"
                ) from None
    raise AssertionError("unreachable")


@transaction.atomic
def _reprice_once(variant_id, cause, actor, force, now, rule_set) -> RepriceResult:
    now = now or timezone.now()
    variant = (
        ProductVariant.objects.select_for_update()
        .select_related("product__category", "product__brand", "source")
        .filter(pk=variant_id, is_active=True)
        .first()
    )
    if variant is None:
        return RepriceResult("skipped", "variant_missing_or_inactive")
    existing = VariantPrice.objects.select_for_update().filter(variant=variant).first()
    if existing is not None and existing.origin == PriceOrigin.MANUAL and not force:
        return RepriceResult("skipped", "manual_price")
    try:
        rule_set = rule_set or active_ruleset(now)
    except PriceUnavailable as exc:
        return _blocked(variant, existing, cause, actor, now, None, exc.blockers, {})
    inp, reason = inputs.build_input(variant, rule_set, presentation_currency="IRR", at=now)
    if inp is None:
        return _blocked(
            variant, existing, cause, actor, now, rule_set, [{"code": reason, "message": "No source price."}], {}
        )
    calc = calculate(inp, rule_set)
    if not calc.publishable:
        return _blocked(
            variant, existing, cause, actor, now, rule_set, [b.as_dict() for b in calc.blockers], calc.as_dict()
        )

    final = calc.unit_price_irr
    list_irr, discount = final, Decimal("0.00")
    if inp.list_amount is not None and inp.list_amount > inp.original_amount:
        list_calc = calculate(PriceInput_copy(inp, inp.list_amount), rule_set)
        if list_calc.publishable and list_calc.unit_price_irr > final:
            list_irr = list_calc.unit_price_irr
            discount = ((list_irr - final) / list_irr * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    breakdown = customer_breakdown(calc, rule_set.customer_breakdown)
    changed = existing is None or (existing.final_irr, existing.list_irr, existing.origin) != (
        final,
        list_irr,
        PriceOrigin.PRICING_ENGINE,
    )
    old_final, old_list = (existing.final_irr, existing.list_irr) if existing else (None, None)
    if existing is None:
        VariantPrice.objects.create(
            variant=variant,
            original_amount=inp.original_amount,
            original_currency=inp.original_currency,
            list_irr=list_irr,
            final_irr=final,
            discount_percent=discount,
            origin=PriceOrigin.PRICING_ENGINE,
            breakdown=breakdown,
            computed_at=now,
        )
    else:
        existing.original_amount, existing.original_currency = inp.original_amount, inp.original_currency
        existing.list_irr, existing.final_irr, existing.discount_percent = list_irr, final, discount
        existing.origin, existing.breakdown, existing.computed_at = PriceOrigin.PRICING_ENGINE, breakdown, now
        existing.save()
    if not changed:
        return RepriceResult("unchanged", final_irr=final)
    history = _history(
        variant,
        PriceOutcome.APPLIED,
        cause if existing else PriceCause.INITIAL,
        actor,
        now,
        rule_set,
        calc.rate,
        [],
        calc.as_dict(),
        old_final,
        final,
        old_list,
        list_irr,
    )
    return RepriceResult("applied", final_irr=final, history_id=history.pk)


def PriceInput_copy(inp, amount):  # noqa: N802 - tiny helper kept next to its only caller
    from dataclasses import replace

    return replace(inp, original_amount=amount, list_amount=None)


def _blocked(variant, existing, cause, actor, now, rule_set, blockers, calculation) -> RepriceResult:
    codes = sorted({b["code"] for b in blockers})
    last = PriceHistory.objects.filter(variant=variant).order_by("-sequence").first()
    if (
        last is not None
        and last.outcome == PriceOutcome.BLOCKED
        and sorted({b["code"] for b in last.blockers}) == codes
        and last.rule_version == (rule_set.version if rule_set else None)
    ):
        return RepriceResult("blocked", ",".join(codes))  # same reason as last time: do not flood the history
    history = _history(
        variant,
        PriceOutcome.BLOCKED,
        cause,
        actor,
        now,
        rule_set,
        None,
        blockers,
        calculation,
        existing.final_irr if existing else None,
        None,
        existing.list_irr if existing else None,
        None,
    )
    logger.info("reprice_blocked", variant=variant.sku, codes=codes)
    return RepriceResult("blocked", ",".join(codes), history_id=history.pk)


def _history(
    variant, outcome, cause, actor, now, rule_set, rate, blockers, calculation, old_final, new_final, old_list, new_list
):
    last = (
        PriceHistory.objects.filter(variant=variant).order_by("-sequence").values_list("sequence", flat=True).first()
        or 0
    )
    return PriceHistory.objects.create(
        variant=variant,
        sequence=last + 1,
        outcome=outcome,
        cause=cause,
        old_final_irr=old_final,
        new_final_irr=new_final,
        old_list_irr=old_list,
        new_list_irr=new_list,
        rule_version=rule_set.version if rule_set else None,
        rate_id=getattr(rate, "rate_id", None),
        rate_at=getattr(rate, "rate_at", None),
        blockers=blockers,
        calculation=calculation,
        input_hash=_hash({"calc": calculation, "blockers": blockers}),
        actor=actor if getattr(actor, "pk", None) else None,
        created_at=now,
    )


def reprice_variants(variant_ids, **kwargs) -> dict:
    counts = {"applied": 0, "unchanged": 0, "blocked": 0, "skipped": 0, "errors": 0}
    for pk in variant_ids:
        try:
            counts[reprice_variant(pk, **kwargs).outcome] += 1
        except Exception as exc:  # noqa: BLE001 - one variant must not stop the batch
            counts["errors"] += 1
            logger.error("reprice_failed", variant=pk, exc_type=type(exc).__name__)
    return counts


def variants_for_currencies(currencies) -> list[int]:
    from apps.product_sources.models import ExternalProductVariant

    return list(
        ExternalProductVariant.objects.filter(
            is_active=True, canonical_variant__isnull=False, current_price__currency_code__in=list(currencies)
        )
        .values_list("canonical_variant_id", flat=True)
        .distinct()
    )
