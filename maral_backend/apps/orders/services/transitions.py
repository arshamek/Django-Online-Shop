"""
The ONLY way an order changes status. Validates the edge against orders.state, applies the side effects
(stock, procurement, timestamps, refund bookkeeping) and writes the history row, all in one transaction.
"""

from decimal import Decimal

import structlog
from django.db import transaction
from django.utils import timezone

from apps.common.exceptions import BusinessRuleViolation, Conflict
from apps.inventory.services import stock

from .. import state
from ..models import ActorType, Order, OrderStatusHistory, ProcurementRecord, ProcurementStatus
from ..state import OrderStatus as S
from ..state import OrderType

logger = structlog.get_logger("maral.orders")


def has_captured_payment(order: Order) -> bool:
    from apps.payments.models import PaymentStatus

    return order.paid_at is not None or order.checkout.payments.filter(status=PaymentStatus.CAPTURED).exists()


def _procurement(order, status, **extra):
    ProcurementRecord.objects.filter(order_item__order=order).update(status=status, **extra)


@transaction.atomic
def transition(
    order: Order,
    to_status: str,
    *,
    actor_type: str,
    actor=None,
    reason: str = "",
    metadata: dict | None = None,
    updates: dict | None = None,
    now=None,
) -> Order:
    order = Order.objects.select_for_update().get(pk=order.pk)  # no joins under a row lock (SQL Server lock hints)
    current = order.status
    metadata = dict(metadata or {})
    if not state.can_transition(order.order_type, current, to_status):
        raise Conflict(f"An order cannot move from {current} to {to_status}.", code="invalid_transition")
    now = now or timezone.now()
    fields = {"status", "updated_at"}
    order.status = to_status

    for key, value in (updates or {}).items():
        setattr(order, key, value)
        fields.add(key)

    if to_status == S.PAID:
        order.paid_at = now
        fields.add("paid_at")
    elif to_status == S.PURCHASED:
        missing = ProcurementRecord.objects.filter(order_item__order=order, source_order_ref="")
        ref = str(metadata.get("source_order_ref", "")).strip()
        if missing.exists() and not ref:
            raise BusinessRuleViolation("The source store's order reference is required.", code="source_order_ref_required")
        if ref:
            missing.update(source_order_ref=ref[:120])
    elif to_status == S.SHIPPED:
        from apps.shipping.services import shipments

        order.shipped_at = now
        order.tracking_carrier = str(metadata.get("tracking_carrier", order.tracking_carrier))[:60]
        order.tracking_number = str(metadata.get("tracking_number", order.tracking_number))[:80]
        shipments.assert_can_ship(order, order.tracking_number)  # a method that requires tracking cannot ship without it
        fields |= {"shipped_at", "tracking_carrier", "tracking_number"}
    elif to_status == S.DELIVERED and current != S.REFUND_PENDING:
        order.delivered_at = now
        fields.add("delivered_at")
    elif to_status == S.CANCELLED and current != S.REFUND_PENDING:
        order.cancelled_at, order.cancel_reason = now, reason[:300]
        fields |= {"cancelled_at", "cancel_reason"}

    order._status_write_allowed = True
    order.save(update_fields=sorted(fields))
    OrderStatusHistory.objects.create(
        order=order, from_status=current, to_status=to_status, actor_type=actor_type,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        reason=reason[:300], metadata=metadata, created_at=now,
    )  # fmt: skip

    # ---- side effects that follow the new status
    if to_status == S.PAID and order.order_type == OrderType.DIRECT_INVENTORY:
        stock.commit("order", str(order.public_id))
    elif to_status == S.PAID:
        _procurement(order, ProcurementStatus.QUEUED)
    elif to_status == S.PURCHASE_PENDING:
        _procurement(order, ProcurementStatus.QUEUED)
    elif to_status == S.PURCHASED:
        ProcurementRecord.objects.filter(order_item__order=order).update(status=ProcurementStatus.PURCHASED, purchased_at=now, buyer=actor if getattr(actor, "is_authenticated", False) else None)
    elif to_status == S.CANCELLED and current != S.REFUND_PENDING:
        _on_cancelled(order, current, actor_type, actor, reason)
    elif to_status == S.SHIPPED:
        from apps.shipping.services import shipments

        shipments.on_order_shipped(order, actor_type, actor)
    elif to_status == S.DELIVERED and current != S.REFUND_PENDING:
        from apps.shipping.services import shipments

        shipments.on_order_delivered(order, actor)
    order.refresh_from_db()
    logger.info("order_transition", order=order.code, from_status=current, to_status=to_status, actor_type=actor_type)
    return order


def _on_cancelled(order: Order, previous: str, actor_type, actor, reason):
    if order.order_type == OrderType.PROXY_PURCHASE:
        _procurement(order, ProcurementStatus.CANCELLED if previous != S.PURCHASE_PENDING else ProcurementStatus.SOURCE_UNAVAILABLE)
    if previous == S.PENDING_PAYMENT:
        stock.release("order", str(order.public_id), reason="order cancelled before payment")
    elif order.order_type == OrderType.DIRECT_INVENTORY:
        stock.return_committed("order", str(order.public_id), reason="order cancelled after payment")
    if previous != S.PENDING_PAYMENT:
        from . import refunds

        refunds.open_system_refund(order, kind="cancellation", reason=reason or "order cancelled", actor_type=actor_type, actor=actor)


def next_options(order: Order) -> list[str]:
    return sorted(state.allowed_next(order.order_type, order.status))
