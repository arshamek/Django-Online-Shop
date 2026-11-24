"""
Order state machine (docs/orders/ORDER_STATE_MACHINE.md). The ONLY place that says which status may follow which.
`services.transitions.transition()` is the only writer of `Order.status`; nothing a client sends ever sets it.

Two business models share one spine:
  DIRECT_INVENTORY  PENDING_PAYMENT > PAID > PROCESSING > READY_TO_SHIP > SHIPPED > DELIVERED
  PROXY_PURCHASE    PENDING_PAYMENT > PAID > PROCESSING > PURCHASE_PENDING > PURCHASED > IN_TRANSIT
                    > [CUSTOMS_PROCESSING] > READY_TO_SHIP > SHIPPED > DELIVERED
Money back: CANCELLED | DELIVERED | PARTIALLY_REFUNDED > REFUND_PENDING > PARTIALLY_REFUNDED | REFUNDED
"""

from django.db import models


class OrderType(models.TextChoices):
    DIRECT_INVENTORY = "DIRECT_INVENTORY", "Direct inventory"
    PROXY_PURCHASE = "PROXY_PURCHASE", "Proxy purchase"


class OrderStatus(models.TextChoices):
    PENDING_PAYMENT = "PENDING_PAYMENT", "Pending payment"
    PAID = "PAID", "Paid"
    PROCESSING = "PROCESSING", "Processing"
    PURCHASE_PENDING = "PURCHASE_PENDING", "Purchase pending"
    PURCHASED = "PURCHASED", "Purchased from the source store"
    IN_TRANSIT = "IN_TRANSIT", "In international transit"
    CUSTOMS_PROCESSING = "CUSTOMS_PROCESSING", "Customs processing"
    READY_TO_SHIP = "READY_TO_SHIP", "Ready to ship"
    SHIPPED = "SHIPPED", "Shipped"
    DELIVERED = "DELIVERED", "Delivered"
    CANCELLED = "CANCELLED", "Cancelled"
    REFUND_PENDING = "REFUND_PENDING", "Refund pending"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED", "Partially refunded"
    REFUNDED = "REFUNDED", "Refunded"


S = OrderStatus

# Every edge of the machine. Type-specific edges are filtered by `allowed_next`.
TRANSITIONS: dict[str, frozenset[str]] = {
    S.PENDING_PAYMENT: frozenset({S.PAID, S.CANCELLED}),
    S.PAID: frozenset({S.PROCESSING, S.CANCELLED}),
    S.PROCESSING: frozenset({S.PURCHASE_PENDING, S.READY_TO_SHIP, S.CANCELLED}),
    S.PURCHASE_PENDING: frozenset({S.PURCHASED, S.CANCELLED}),  # CANCELLED: source out of stock / price change
    S.PURCHASED: frozenset({S.IN_TRANSIT}),
    S.IN_TRANSIT: frozenset({S.CUSTOMS_PROCESSING, S.READY_TO_SHIP}),
    S.CUSTOMS_PROCESSING: frozenset({S.READY_TO_SHIP}),
    S.READY_TO_SHIP: frozenset({S.SHIPPED}),
    S.SHIPPED: frozenset({S.DELIVERED}),
    S.DELIVERED: frozenset({S.REFUND_PENDING}),
    S.CANCELLED: frozenset({S.REFUND_PENDING}),  # only when money was captured (service checks)
    S.REFUND_PENDING: frozenset({S.PARTIALLY_REFUNDED, S.REFUNDED, S.DELIVERED, S.CANCELLED}),  # back = refund rejected
    S.PARTIALLY_REFUNDED: frozenset({S.REFUND_PENDING, S.REFUNDED}),
    S.REFUNDED: frozenset(),
}

PROXY_ONLY = frozenset({S.PURCHASE_PENDING, S.PURCHASED, S.IN_TRANSIT, S.CUSTOMS_PROCESSING})
# A direct order goes PROCESSING > READY_TO_SHIP; a proxy order must be bought and shipped in first.
DIRECT_FORBIDDEN_EDGES = frozenset({(S.PROCESSING, S.PURCHASE_PENDING)})
PROXY_FORBIDDEN_EDGES = frozenset({(S.PROCESSING, S.READY_TO_SHIP)})

TERMINAL = frozenset(s for s, nxt in TRANSITIONS.items() if not nxt)
# Customer may cancel by themselves only before anything is packed or bought.
CUSTOMER_CANCELLABLE = frozenset({S.PENDING_PAYMENT, S.PAID, S.PROCESSING})
# Money has been captured for the order from PAID onwards.
UNPAID = frozenset({S.PENDING_PAYMENT})
REFUND_ORIGINS = frozenset({S.CANCELLED, S.DELIVERED, S.PARTIALLY_REFUNDED})


def allowed_next(order_type: str, status: str) -> frozenset[str]:
    nxt = TRANSITIONS.get(status, frozenset())
    if order_type == OrderType.DIRECT_INVENTORY:
        return frozenset(t for t in nxt if t not in PROXY_ONLY and (status, t) not in DIRECT_FORBIDDEN_EDGES)
    return frozenset(t for t in nxt if (status, t) not in PROXY_FORBIDDEN_EDGES)


def can_transition(order_type: str, current: str, target: str) -> bool:
    return target in allowed_next(order_type, current)


# Customer-facing progress path (tracking screen).
def progress_path(order_type: str) -> list[str]:
    if order_type == OrderType.DIRECT_INVENTORY:
        return [S.PENDING_PAYMENT, S.PAID, S.PROCESSING, S.READY_TO_SHIP, S.SHIPPED, S.DELIVERED]
    return [
        S.PENDING_PAYMENT, S.PAID, S.PROCESSING, S.PURCHASE_PENDING, S.PURCHASED, S.IN_TRANSIT,
        S.CUSTOMS_PROCESSING, S.READY_TO_SHIP, S.SHIPPED, S.DELIVERED,
    ]  # fmt: skip
