"""Customer-facing order shapes. Internal reasons, staff identities, profit and cost data never appear here."""

from decimal import Decimal

from apps.catalog.context import money
from apps.pricing.models import BreakdownVisibility

from . import labels, state
from .models import OPEN_REFUND, Order, OrderItem
from .services import lifecycle, refunds


def _lang(ctx) -> str:
    return "fa" if ctx.lang == "fa" else "en"


def _item(item: OrderItem, cur: str, show: str) -> dict:
    row = {
        "id": str(item.public_id),
        "product_name": item.product_name,
        "brand": item.brand_name,
        "sku": item.sku,
        "size": {"code": item.size_code, "name": item.size_name} if item.size_code else None,
        "color": {"code": item.color_code, "name": item.color_name} if item.color_code else None,
        "image": item.image_url or None,
        "quantity": item.quantity,
        "fulfillment_type": item.fulfillment_type,
        "source": item.source_code,
        "unit_price": money(item.unit_price_irr, cur),
        "list_unit_price": money(item.list_unit_irr, cur),
        "discount": money(item.discount_unit_irr * item.quantity, cur),
        "line_price": money(item.line_price_irr, cur),
        "separate_charges": money(item.line_separate_irr, cur),
        "line_total": money(item.line_total_irr, cur),
        "expected_delivery": None
        if item.expected_days_min is None
        else {"min_days": item.expected_days_min, "max_days": item.expected_days_max, "unit": item.expected_days_unit},
        "has_estimated_costs": item.has_estimated_costs,
    }
    if show != BreakdownVisibility.NONE:  # the owner decides how much of the price build-up customers may see
        row["source_price"] = {"amount": str(item.original_amount.normalize()), "currency": item.original_currency}
        row["exchange_rate"] = {"rate": str(item.exchange_rate.normalize()), "source": item.exchange_rate_source, "at": item.exchange_rate_at}
    if show == BreakdownVisibility.ITEMIZED:
        row["fees"] = money(item.fees_unit_irr * item.quantity, cur)
        row["freight"] = money(item.freight_unit_irr * item.quantity, cur)
        row["customs"] = money(item.customs_unit_irr * item.quantity, cur)
    return row


def actions(order: Order) -> dict:
    eligibility = refunds.return_eligibility(order)
    return {
        "can_cancel": lifecycle.customer_can_cancel(order),
        "can_request_refund": eligibility["eligible"],
        "refund_blocked_reason": None if eligibility["eligible"] else eligibility["code"],
        "return_deadline": eligibility["deadline"],
        "can_pay": order.status == "PENDING_PAYMENT",
    }


def order_summary(order: Order, ctx) -> dict:
    cur = ctx.currency
    return {
        "id": str(order.public_id),
        "code": order.code,
        "order_type": order.order_type,
        "status": order.status,
        "status_label": labels.label(order.status, _lang(ctx)),
        "placed_at": order.placed_at,
        "item_count": sum(i.quantity for i in order.items.all()),
        "total": money(order.total_irr, cur),
        "currency": cur,
    }


def delivery_info(order: Order) -> dict:
    """What the customer was told at checkout. A range exists only if one was configured; never an exact date."""
    snap = order.shipping_snapshot or {}
    customs = dict(snap.get("customs") or {})
    customs.pop("amount_irr", None)
    return {
        "method": order.shipping_method_code,
        "label": order.shipping_label,
        "estimate": snap.get("delivery") or {"status": "unknown", "min_days": None, "max_days": None, "unit": "business_days"},
        "customs": customs or None,
        "zone": snap.get("zone") or None,
    }


def order_detail(order: Order, ctx) -> dict:
    cur = ctx.currency
    show = order.quote.rule_set.customer_breakdown
    refund_rows = list(order.refunds.all())
    return {
        **order_summary(order, ctx),
        "checkout_id": str(order.checkout.public_id),
        "items": [_item(i, cur, show) for i in order.items.all()],
        "shipping_address": order.shipping_address,
        "delivery": {"method": order.shipping_method_code, "label": order.shipping_label, **{k: v for k, v in delivery_info(order).items() if k in ("estimate", "customs")}},
        "totals": {
            "items": money(order.items_subtotal_irr, cur),
            "separate_charges": money(order.separate_charges_irr, cur),
            "shipping": money(order.shipping_irr, cur),
            "discount_included": money(order.discount_irr, cur),
            "total": money(order.total_irr, cur),
            "refunded": money(order.refunded_irr, cur),
        },
        "has_estimated_costs": order.has_estimated_costs,
        "payment_due_at": order.payment_due_at if order.status == "PENDING_PAYMENT" else None,
        "paid_at": order.paid_at,
        "shipped_at": order.shipped_at,
        "delivered_at": order.delivered_at,
        "cancelled_at": order.cancelled_at,
        "tracking": {"carrier": order.tracking_carrier, "number": order.tracking_number} if order.tracking_number else None,
        "refunds": [refund_out(r, cur) for r in refund_rows],
        "actions": actions(order),
    }


def refund_out(refund, cur: str) -> dict:
    return {
        "id": str(refund.public_id),
        "kind": refund.kind,
        "status": refund.status,
        "amount": money(refund.amount_irr, cur),
        "created_at": refund.created_at,
        "completed_at": refund.completed_at,
        "items": [{"order_item_id": str(i.order_item.public_id), "quantity": i.quantity} for i in refund.items.select_related("order_item")],
    }


def history(order: Order, ctx) -> list[dict]:
    lang = _lang(ctx)
    return [
        {"status": h.to_status, "label": labels.label(h.to_status, lang), "at": h.created_at}
        for h in order.history.all()  # reasons and actors are internal
    ]


def tracking(order: Order, ctx) -> dict:
    lang = _lang(ctx)
    visited = {h.to_status for h in order.history.all()}
    path = state.progress_path(order.order_type)
    steps = [
        {"status": s, "label": labels.label(s, lang), "done": s in visited and s != order.status, "current": s == order.status}
        for s in path
    ]
    closed = order.status in (state.OrderStatus.CANCELLED, state.OrderStatus.REFUND_PENDING, state.OrderStatus.PARTIALLY_REFUNDED, state.OrderStatus.REFUNDED)
    items = list(order.items.all())
    mins = [i.expected_days_min for i in items if i.expected_days_min is not None]
    maxs = [i.expected_days_max for i in items if i.expected_days_max is not None]
    return {
        "code": order.code,
        "status": order.status,
        "status_label": labels.label(order.status, lang),
        "closed": closed,
        "steps": steps,
        "events": history(order, ctx),
        "estimated_delivery": None
        if not maxs
        else {"min_days": min(mins), "max_days": max(maxs), "unit": items[0].expected_days_unit, "from": order.paid_at or order.placed_at},
        "carrier": order.tracking_carrier or None,
        "tracking_number": order.tracking_number or None,
        "shipments": [_shipment_brief(s) for s in order.shipments.all()],
    }


def _shipment_brief(s) -> dict:
    return {"id": str(s.public_id), "status": s.status, "carrier": s.carrier or None, "tracking_number": s.tracking_number or None, "tracking_url": s.tracking_url or None}
