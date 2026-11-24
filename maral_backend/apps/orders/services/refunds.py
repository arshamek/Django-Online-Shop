"""
Refunds and return eligibility. Amounts are computed here from the order's immutable snapshot; a customer can only
say WHICH items (and why), never how much. Money actually moving back is recorded by `complete`.
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.common.exceptions import BusinessRuleViolation, Conflict
from apps.shipping.services.methods import allocate

from .. import state
from ..models import (
    COUNTING_REFUND,
    OPEN_REFUND,
    ActorType,
    Order,
    OrderItem,
    Refund,
    RefundItem,
    RefundKind,
    RefundStatus,
)
from ..state import OrderStatus as S
from . import transitions


# ------------------------------------------------------------------------------------------------ reads
def open_refund_total(order: Order) -> Decimal:
    return Refund.objects.filter(order=order, status__in=OPEN_REFUND).aggregate(t=Sum("amount_irr"))["t"] or Decimal(0)


def refundable_amount(order: Order) -> Decimal:
    return max(order.total_irr - order.refunded_irr - open_refund_total(order), Decimal(0))


def refunded_quantities(order: Order) -> dict[int, int]:
    rows = (
        RefundItem.objects.filter(refund__order=order, refund__status__in=COUNTING_REFUND)
        .values("order_item_id")
        .annotate(q=Sum("quantity"))
    )
    return {r["order_item_id"]: r["q"] for r in rows}


def return_eligibility(order: Order, now=None) -> dict:
    """{eligible, code, deadline}. `code` explains a refusal (customer-safe)."""
    now = now or timezone.now()
    days = settings.ORDER_RETURN_WINDOW_DAYS
    if days <= 0:
        return {"eligible": False, "code": "returns_disabled", "deadline": None}
    if order.status not in (S.DELIVERED, S.PARTIALLY_REFUNDED) or order.delivered_at is None:
        return {"eligible": False, "code": "not_delivered", "deadline": None}
    deadline = order.delivered_at + timedelta(days=days)
    if now > deadline:
        return {"eligible": False, "code": "return_window_closed", "deadline": deadline}
    if Refund.objects.filter(order=order, status__in=OPEN_REFUND).exists():
        return {"eligible": False, "code": "refund_already_open", "deadline": deadline}
    done = refunded_quantities(order)
    if all(done.get(i.pk, 0) >= i.quantity for i in order.items.all()):
        return {"eligible": False, "code": "nothing_left_to_return", "deadline": deadline}
    return {"eligible": True, "code": "", "deadline": deadline}


# ------------------------------------------------------------------------------------------------ amounts
def _shipping_share(order: Order, items: list[OrderItem]) -> dict[int, Decimal]:
    """Delivery fee spread over the order's lines (by line price) so partial returns get an exact share."""
    shares = allocate(order.shipping_irr, [i.line_price_irr for i in items])
    return {i.pk: s for i, s in zip(items, shares)}


def item_refund_amount(order: Order, item: OrderItem, already: int, quantity: int, line_ship: Decimal) -> Decimal:
    goods = (item.unit_price_irr + item.unit_separate_irr) * quantity
    before = (line_ship * already / item.quantity).to_integral_value(rounding="ROUND_DOWN")
    after = (line_ship * (already + quantity) / item.quantity).to_integral_value(rounding="ROUND_DOWN")
    return goods + (after - before)


# ------------------------------------------------------------------------------------------------ requests
def _new_refund(order, *, kind, amount, reason, actor_type, actor, resume_status) -> Refund:
    return Refund.objects.create(
        order=order, kind=kind, amount_irr=amount, reason=reason[:500], resume_status=resume_status,
        requested_by_type=actor_type, requested_by=actor if getattr(actor, "is_authenticated", False) else None,
    )  # fmt: skip


@transaction.atomic
def request_return(order: Order, user, lines: list[tuple], reason: str) -> Refund:
    """`lines` = [(OrderItem public_id, quantity)]. Eligibility and amount are decided here."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    check = return_eligibility(order)
    if not check["eligible"]:
        raise BusinessRuleViolation("This order is not eligible for a return refund.", code=check["code"])
    if not reason.strip():
        raise BusinessRuleViolation("A reason is required.", code="reason_required")
    items = list(order.items.all())
    by_public = {str(i.public_id): i for i in items}
    done = refunded_quantities(order)
    ship = _shipping_share(order, items)
    non_returnable = set(settings.ORDER_NON_RETURNABLE_CATEGORY_SLUGS)
    amount, seen, picked = Decimal(0), set(), []
    for public_id, quantity in lines:
        item = by_public.get(str(public_id))
        if item is None or item.pk in seen:
            raise BusinessRuleViolation("Unknown or repeated order item.", code="invalid_item")
        seen.add(item.pk)
        if item.category_slug and item.category_slug in non_returnable:
            raise BusinessRuleViolation("This item cannot be returned.", code="item_not_returnable")
        remaining = item.quantity - done.get(item.pk, 0)
        if quantity < 1 or quantity > remaining:
            raise BusinessRuleViolation(f"You can return at most {remaining} of this item.", code="invalid_quantity")
        amount += item_refund_amount(order, item, done.get(item.pk, 0), quantity, ship[item.pk])
        picked.append((item, quantity))
    if not picked:
        raise BusinessRuleViolation("Choose at least one item.", code="invalid_item")
    if amount > refundable_amount(order):
        raise BusinessRuleViolation("The amount exceeds what can still be refunded.", code="refund_exceeds_order")
    refund = _new_refund(order, kind=RefundKind.RETURN, amount=amount, reason=reason, actor_type=ActorType.CUSTOMER, actor=user, resume_status=order.status)
    RefundItem.objects.bulk_create([RefundItem(refund=refund, order_item=i, quantity=q) for i, q in picked])
    transitions.transition(order, S.REFUND_PENDING, actor_type=ActorType.CUSTOMER, actor=user, reason="return requested", metadata={"refund": str(refund.public_id)})
    return refund


@transaction.atomic
def open_system_refund(order: Order, *, kind, reason, actor_type, actor=None) -> Refund | None:
    """Cancellation after payment / late payment: the whole remaining amount goes back."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if not transitions.has_captured_payment(order):
        return None
    amount = refundable_amount(order)
    if amount <= 0 or order.status not in (S.CANCELLED,):
        return None
    refund = _new_refund(order, kind=kind, amount=amount, reason=reason, actor_type=actor_type, actor=actor, resume_status=S.CANCELLED)
    transitions.transition(order, S.REFUND_PENDING, actor_type=actor_type, actor=actor, reason=f"{kind} refund opened", metadata={"refund": str(refund.public_id)})
    return refund


@transaction.atomic
def staff_refund(order: Order, staff, amount: Decimal, reason: str) -> Refund:
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status not in state.REFUND_ORIGINS:
        raise BusinessRuleViolation("A refund can only be opened for a cancelled, delivered or partly refunded order.", code="refund_not_allowed")
    if order.status == S.CANCELLED and not transitions.has_captured_payment(order):
        raise BusinessRuleViolation("No money was captured for this order.", code="nothing_to_refund")
    if Refund.objects.filter(order=order, status__in=OPEN_REFUND).exists():
        raise Conflict("A refund is already open for this order.", code="refund_already_open")
    if amount <= 0 or amount != amount.to_integral_value() or amount > refundable_amount(order):
        raise BusinessRuleViolation("The amount exceeds what can still be refunded.", code="refund_exceeds_order")
    refund = _new_refund(order, kind=RefundKind.STAFF, amount=amount, reason=reason, actor_type=ActorType.STAFF, actor=staff, resume_status=order.status)
    transitions.transition(order, S.REFUND_PENDING, actor_type=ActorType.STAFF, actor=staff, reason="staff refund", metadata={"refund": str(refund.public_id)})
    return refund


# ------------------------------------------------------------------------------------------------ decisions
def _locked(refund: Refund) -> Refund:
    return Refund.objects.select_for_update().get(pk=refund.pk)


@transaction.atomic
def approve(refund: Refund, staff, note: str = "") -> Refund:
    refund = _locked(refund)
    if refund.status != RefundStatus.REQUESTED:
        raise Conflict("Only a requested refund can be approved.", code="invalid_refund_state")
    refund.status, refund.decided_by, refund.decided_at, refund.decision_note = RefundStatus.APPROVED, staff, timezone.now(), note[:500]
    refund.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    return refund


@transaction.atomic
def reject(refund: Refund, staff, note: str) -> Refund:
    refund = _locked(refund)
    if refund.status != RefundStatus.REQUESTED:
        raise Conflict("Only a requested refund can be rejected.", code="invalid_refund_state")
    if refund.kind in (RefundKind.CANCELLATION, RefundKind.LATE_PAYMENT):
        raise BusinessRuleViolation("Money captured for a cancelled order must be returned.", code="cannot_reject")
    if not note.strip():
        raise BusinessRuleViolation("A reason is required.", code="reason_required")
    refund.status, refund.decided_by, refund.decided_at, refund.decision_note = RefundStatus.REJECTED, staff, timezone.now(), note[:500]
    refund.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    order = Order.objects.get(pk=refund.order_id)
    if not Refund.objects.filter(order=order, status__in=OPEN_REFUND).exists():
        back = refund.resume_status
        transitions.transition(order, back, actor_type=ActorType.STAFF, actor=staff, reason="refund rejected", metadata={"refund": str(refund.public_id)})
    return refund


@transaction.atomic
def complete(refund: Refund, staff, provider_ref: str = "") -> Refund:
    """Money has gone back to the customer: book it and move the order to PARTIALLY_REFUNDED / REFUNDED."""
    refund = _locked(refund)
    if refund.status != RefundStatus.APPROVED:
        raise Conflict("Only an approved refund can be completed.", code="invalid_refund_state")
    order = Order.objects.select_for_update().get(pk=refund.order_id)
    new_total = order.refunded_irr + refund.amount_irr
    if new_total > order.total_irr:
        raise BusinessRuleViolation("Refunds would exceed the order total.", code="refund_exceeds_order")
    refund.status, refund.completed_at, refund.provider_ref = RefundStatus.COMPLETED, timezone.now(), provider_ref[:120]
    refund.save(update_fields=["status", "completed_at", "provider_ref"])
    target = S.REFUNDED if new_total == order.total_irr else S.PARTIALLY_REFUNDED
    transitions.transition(
        order, target, actor_type=ActorType.STAFF, actor=staff, reason="refund completed",
        metadata={"refund": str(refund.public_id), "amount_irr": str(refund.amount_irr)},
        updates={"refunded_irr": new_total},
    )  # fmt: skip
    return refund


def execute_with_provider(refund: Refund, staff) -> Refund:
    """Ask the payment provider to send the money back (only providers that support it), then complete."""
    from apps.payments.models import PaymentStatus
    from apps.payments.providers import registry

    refund = Refund.objects.select_related("order__checkout").get(pk=refund.pk)
    payment = refund.order.checkout.payments.filter(status=PaymentStatus.CAPTURED).first()
    if payment is None:
        raise BusinessRuleViolation("No captured payment found.", code="nothing_to_refund")
    provider = registry.available().get(payment.provider)
    if provider is None or not provider.supports_refund:
        raise BusinessRuleViolation("This provider needs a manual refund; record it with 'complete'.", code="manual_refund_required")
    result = provider.refund(payment, refund.amount_irr, str(refund.public_id))
    return complete(refund, staff, provider_ref=result.provider_ref)
