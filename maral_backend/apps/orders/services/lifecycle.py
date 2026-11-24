"""Events that move orders without a staff click: payment captured, payment window over, customer cancellation."""

from datetime import timedelta

import structlog
from django.db import transaction
from django.utils import timezone

from apps.common.exceptions import BusinessRuleViolation

from .. import state
from ..models import ActorType, Checkout, CheckoutStatus, Order
from ..state import OrderStatus as S
from . import refunds, transitions

logger = structlog.get_logger("maral.orders")


@transaction.atomic
def on_payment_captured(payment) -> None:
    """Called by payments.services.capture after server-side verification."""
    orders = list(Order.objects.select_for_update().filter(checkout=payment.checkout).order_by("id"))
    for order in orders:
        if order.status == S.PENDING_PAYMENT:
            transitions.transition(order, S.PAID, actor_type=ActorType.PAYMENT, reason="payment captured", metadata={"payment": str(payment.public_id)})
        elif order.status == S.CANCELLED and not order.refunds.exists():
            # The customer paid for an order that was already cancelled (timeout / own cancellation): give it back.
            refunds.open_system_refund(order, kind="late_payment", reason="payment arrived after cancellation", actor_type=ActorType.PAYMENT)


def customer_can_cancel(order: Order) -> bool:
    return order.status in state.CUSTOMER_CANCELLABLE


def customer_cancel(order: Order, user, reason: str = "") -> Order:
    """Cancellation rule: allowed until packing / purchasing starts (PENDING_PAYMENT, PAID, PROCESSING)."""
    locked = Order.objects.get(pk=order.pk)
    if not customer_can_cancel(locked):
        raise BusinessRuleViolation("This order can no longer be cancelled online. Contact support.", code="cancellation_not_allowed")
    return transitions.transition(locked, S.CANCELLED, actor_type=ActorType.CUSTOMER, actor=user, reason=reason or "cancelled by customer")


def expire_unpaid(now=None) -> int:
    """Payment window over: cancel the order (stock is released by the transition) and close the checkout."""
    from apps.payments.services import expire_due as expire_payments

    now = now or timezone.now()
    count = 0
    due = Order.objects.filter(status=S.PENDING_PAYMENT, payment_due_at__lte=now).order_by("id")
    for order in due:
        try:
            transitions.transition(order, S.CANCELLED, actor_type=ActorType.SYSTEM, reason="payment_timeout", now=now)
            count += 1
        except Exception as exc:  # one bad order must not block the others; it is retried next run
            logger.error("order_expiry_failed", order=order.code, error=type(exc).__name__)
    expire_payments(now)
    return count


def expire_checkouts(now=None) -> int:
    now = now or timezone.now()
    from apps.pricing.services import quotes

    stale = Checkout.objects.filter(status=CheckoutStatus.OPEN, expires_at__lte=now)
    ids = list(stale.values_list("pk", "quote_id"))
    stale.update(status=CheckoutStatus.EXPIRED)
    for _, quote_id in ids:
        quotes.expire_quote(quote_id, now=now)  # open quotes only; locked ones are skipped by the service
    return len(ids)
