"""
Price quotes: an immutable snapshot of one calculation per item. Locked quotes never change, whatever rates or
rules do afterwards. Quotes also never outlive the exchange rate they were built on.
"""

import hashlib
import json
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from apps.common.exceptions import BusinessRuleViolation, Conflict
from apps.currencies.models import Currency

from ..errors import PriceUnavailable
from ..models import PriceQuote, PriceQuoteComponent, PriceQuoteItem, QuoteStatus
from . import inputs
from .engine import calculate
from .rulesets import active_ruleset

MAX_ITEMS = 50
MAX_QTY = 99


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest() if token else ""


def _canonical_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def create_quote(
    *,
    items,
    currency: str,
    user=None,
    guest_token: str = "",
    destination_country: str = "",
    shipping_method: str = "",
    sales_model: str = "",
    now=None,
    rule_set=None,
) -> PriceQuote:
    """`items` = [(ProductVariant, quantity)]. Raises PriceUnavailable (with blockers) when anything cannot be priced."""
    now = now or timezone.now()
    if not items or len(items) > MAX_ITEMS:
        raise BusinessRuleViolation(f"A quote needs 1 to {MAX_ITEMS} items.", code="invalid_items")
    if any(not (1 <= qty <= MAX_QTY) for _, qty in items):
        raise BusinessRuleViolation(f"Quantity must be between 1 and {MAX_QTY}.", code="invalid_quantity")
    rule_set = rule_set or active_ruleset(now)
    display = Currency.objects.filter(code=currency, is_active=True, is_display=True).first()
    if display is None:
        raise BusinessRuleViolation("Unsupported display currency.", code="unsupported_currency")

    calcs, blockers = [], []
    for variant, qty in items:
        inp, reason = inputs.build_input(
            variant,
            rule_set,
            destination_country=destination_country,
            sales_model=sales_model,
            shipping_method=shipping_method,
            presentation_currency=currency,
            at=now,
        )
        if inp is None:
            blockers.append({"variant": variant.sku, "code": reason, "message": "No source price."})
            continue
        calc = calculate(inp, rule_set)
        if not calc.quotable:
            blockers += [{"variant": variant.sku, **b.as_dict()} for b in (*calc.blockers, *calc.display_blockers)]
            continue
        calcs.append((variant, qty, inp, calc))
    if blockers:
        raise PriceUnavailable("A price cannot be given for one or more items right now.", blockers=blockers)

    expiry = now + timedelta(minutes=rule_set.quote_validity_minutes)
    for _, _, _, calc in calcs:
        hint = calc.expires_hint()
        if hint is not None:
            expiry = min(expiry, hint)
    if expiry <= now:
        raise PriceUnavailable(
            "The exchange rate is about to expire.",
            code="rate_expiring",
            blockers=[{"code": "rate_expiring", "message": "The exchange rate is about to expire."}],
        )

    return _persist(
        calcs, rule_set, display, user, guest_token, destination_country, shipping_method, sales_model, now, expiry
    )


@transaction.atomic
def _persist(
    calcs, rule_set, display, user, guest_token, destination, shipping_method, sales_model, now, expiry
) -> PriceQuote:
    quantum = Decimal(1).scaleb(-display.decimal_places) if not display.is_display_unit else None
    subtotal = sum((calc.unit_price_irr * qty for _, qty, _, calc in calcs), Decimal(0))
    separate = sum((calc.unit_separate_irr * qty for _, qty, _, calc in calcs), Decimal(0))
    line_displays = [calc.unit_display * qty for _, qty, _, calc in calcs]
    display_rate = next((c.display_rate for *_, c in calcs if c.display_rate is not None), None)
    separate_display = (
        separate / display.base_factor
        if display.is_display_unit
        else ((separate / display_rate.rate).quantize(quantum, rounding=ROUND_HALF_UP) if display_rate else separate)
    )
    snapshot = [{"sku": v.sku, "qty": q, "calc": c.as_dict()} for v, q, _, c in calcs]
    quote = PriceQuote.objects.create(
        user=user if getattr(user, "is_authenticated", False) else None,
        guest_token_hash=hash_token(guest_token),
        rule_set=rule_set,
        rule_version=rule_set.version,
        display_currency=display,
        destination_country=destination,
        shipping_method=shipping_method,
        sales_model=sales_model,
        subtotal_irr=subtotal,
        separate_irr=separate,
        total_irr=subtotal + separate,
        total_display=sum(line_displays, Decimal(0)) + separate_display,
        display_rate=display_rate.rate if display_rate else None,
        display_rate_source=display_rate.source_code if display_rate else "",
        display_rate_at=display_rate.rate_at if display_rate else None,
        has_estimated_costs=any(c.has_estimated for *_, c in calcs),
        warnings=sorted({w for *_, c in calcs for w in c.warnings}),
        calculation_hash=_canonical_hash({"items": snapshot, "v": rule_set.version}),
        created_at=now,
        expires_at=expiry,
    )
    for variant, qty, _inp, calc in calcs:
        r = calc.rate
        item = PriceQuoteItem.objects.create(
            quote=quote,
            variant=variant,
            sku=variant.sku,
            product_name=_name(variant),
            quantity=qty,
            original_amount=calc.original_amount,
            original_currency=calc.original_currency,
            rate_applied=r.rate,
            rate_source=r.source_code,
            rate_kind=r.source_kind,
            rate_id=r.rate_id,
            rate_at=r.rate_at,
            rate_fetched_at=r.fetched_at,
            rate_is_stale=r.is_stale,
            rate_is_manual=r.is_manual,
            item_irr=calc.item_irr,
            profit_method=calc.profit_method,
            profit_percent=calc.profit_percent,
            profit_irr=calc.profit_irr,
            realized_markup_percent=calc.realized_markup_percent,
            realized_margin_percent=calc.realized_margin_percent,
            rounding_adjustment_irr=calc.rounding_adjustment_irr,
            unit_price_irr=calc.unit_price_irr,
            unit_separate_irr=calc.unit_separate_irr,
            unit_internal_cost_irr=calc.internal_cost_irr,
            unit_realized_profit_irr=calc.realized_profit_irr,
            line_price_irr=calc.unit_price_irr * qty,
            line_separate_irr=calc.unit_separate_irr * qty,
            line_total_irr=calc.unit_total_irr * qty,
            unit_display=calc.unit_display,
            line_display=calc.unit_display * qty,
            has_estimated_costs=calc.has_estimated,
            rule_version=rule_set.version,
            breakdown=calc.as_dict(),
        )
        PriceQuoteComponent.objects.bulk_create(
            [
                PriceQuoteComponent(
                    item=item,
                    sort_order=i,
                    kind=c.kind,
                    code=c.code,
                    name=c.name[:160],
                    basis_irr=c.basis_irr,
                    percent=c.percent,
                    amount_irr=c.amount_irr,
                    in_price=c.in_price and c.charge_mode not in ("absorbed", "separate"),
                    charge_mode=c.charge_mode,
                    value_status=c.value_status,
                    rule_id=c.rule_id,
                )
                for i, c in enumerate(calc.components)
            ]
        )
    verify_quote(quote)
    return quote


def _name(variant) -> str:
    translation = variant.product.translations.order_by("language").first()
    return translation.name if translation else variant.product.slug


def verify_quote(quote: PriceQuote) -> None:
    """Invariants (DOMAIN_MODEL 5.3): sum(components in price) = unit price; line = unit x qty; totals add up."""
    subtotal = separate = Decimal(0)
    for item in quote.items.prefetch_related("components"):
        total = sum((c.amount_irr for c in item.components.all() if c.in_price), Decimal(0))
        if total != item.unit_price_irr:
            raise AssertionError(
                f"quote {quote.public_id}: components {total} != unit price {item.unit_price_irr} ({item.sku})"
            )
        if item.line_price_irr != item.unit_price_irr * item.quantity:
            raise AssertionError(f"quote {quote.public_id}: line total mismatch ({item.sku})")
        subtotal += item.line_price_irr
        separate += item.line_separate_irr
    if (subtotal, separate, subtotal + separate) != (quote.subtotal_irr, quote.separate_irr, quote.total_irr):
        raise AssertionError(f"quote {quote.public_id}: totals mismatch")


# ------------------------------------------------------------------------------------------------ lifecycle
@transaction.atomic
def lock_quote(quote_id, *, reference: str, now=None) -> PriceQuote:
    """Accept a quote for an order. The quote is then frozen forever. Idempotent for the same order reference."""
    now = now or timezone.now()
    quote = PriceQuote.objects.select_for_update().get(pk=quote_id)
    if quote.status == QuoteStatus.LOCKED:
        if quote.locked_reference == reference:
            return quote
        raise Conflict("This quote was accepted by another order.", code="quote_already_locked")
    if quote.status == QuoteStatus.EXPIRED or quote.expires_at <= now:
        raise Conflict("This quote has expired; request a new price.", code="quote_expired")
    quote.status, quote.locked_at, quote.locked_reference = QuoteStatus.LOCKED, now, reference
    quote.save(update_fields=["status", "locked_at", "locked_reference"])
    return quote


@transaction.atomic
def expire_quote(quote_id, *, now=None) -> PriceQuote:
    now = now or timezone.now()
    quote = PriceQuote.objects.select_for_update().get(pk=quote_id)
    if quote.status == QuoteStatus.LOCKED:
        raise Conflict("A locked quote belongs to an order and cannot be expired.", code="quote_locked")
    if quote.status == QuoteStatus.OPEN:
        quote.status, quote.expired_at = QuoteStatus.EXPIRED, now
        quote.save(update_fields=["status", "expired_at"])
    return quote


def expire_due(now=None) -> int:
    """Sweep: open quotes past their expiry become expired. Locked quotes are never touched."""
    now = now or timezone.now()
    return PriceQuote.objects.filter(status=QuoteStatus.OPEN, expires_at__lte=now).update(
        status=QuoteStatus.EXPIRED, expired_at=now
    )
