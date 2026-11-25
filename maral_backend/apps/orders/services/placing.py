"""
Order creation (step 9). One transaction: lock the checkout and cart, re-validate EVERYTHING on the server, lock the
price quote, reserve stock, write orders + immutable snapshots + history, close the checkout and spend the cart.
Any failure rolls the whole thing back (no half-created order, no stuck reservation).
"""

from datetime import timedelta
from decimal import Decimal

import structlog
from django.conf import settings
from django.db import transaction
from django.db.models import Prefetch
from django.utils import timezone
from rest_framework.exceptions import NotFound

from apps.audit import services as audit
from apps.cart.models import Cart, CartStatus
from apps.cart.services import availability, carts
from apps.catalog.models import ProductVariantOption
from apps.common.exceptions import Conflict
from apps.inventory.services import stock
from apps.pricing.services import quotes
from apps.shipping.services.methods import allocate

from ..models import ActorType, Checkout, CheckoutStatus, Order, OrderStatusHistory, ProcurementRecord
from ..state import OrderStatus, OrderType
from . import checkout as checkout_service
from . import codes, snapshots

logger = structlog.get_logger("maral.orders")


def _locked_checkout(user, public_id) -> Checkout:
    checkout = Checkout.objects.select_for_update().filter(public_id=public_id, user=user).first()
    if checkout is None:
        raise NotFound("Checkout not found.")  # someone else's checkout looks exactly like a missing one
    return checkout


def create_orders(user, *, checkout_id, accepted_total_irr=None, now=None) -> list[Order]:
    """Must run inside a transaction (services.idempotency.run provides it)."""
    now = now or timezone.now()
    checkout = _locked_checkout(user, checkout_id)

    if checkout.status == CheckoutStatus.COMPLETED:
        raise Conflict("This checkout already produced orders.", code="checkout_already_completed")
    if checkout.status == CheckoutStatus.INVALIDATED:
        raise Conflict("Your cart changed; review the summary again.", code="cart_changed")
    if checkout.status == CheckoutStatus.EXPIRED or now >= checkout.expires_at:
        raise Conflict("The price expired; request a new summary.", code="quote_expired")

    cart = Cart.objects.select_for_update().get(pk=checkout.cart_id)
    cart_items = list(
        cart.items.select_related("variant__product__brand", "variant__product__category", "variant__price", "variant__source")
        .prefetch_related(
            Prefetch("variant__options", queryset=ProductVariantOption.objects.select_related("attribute", "option").prefetch_related("option__translations")),
            "variant__product__translations",
        )
    )
    pairs = [(i.variant_id, i.quantity) for i in cart_items]
    if cart.status != CartStatus.ACTIVE or cart.version != checkout.cart_version or checkout_service.fingerprint(pairs) != checkout.cart_fingerprint:
        raise Conflict("Your cart changed; review the summary again.", code="cart_changed")

    # The client may only CONFIRM the total it saw; it never sets it.
    if accepted_total_irr is not None and Decimal(accepted_total_irr) != checkout.total_irr:
        raise Conflict("The total changed; review it again.", code="total_mismatch")

    # Availability is re-checked at the moment of ordering (stock may have sold out since the summary).
    issues = availability.assess(pairs, {i.variant_id: i.variant for i in cart_items})
    problems = {str(i.variant.public_id): [x.code for x in issues[i.variant_id]] for i in cart_items if issues[i.variant_id]}
    if problems:
        raise Conflict("Some items are no longer available.", code="items_unavailable", fields=problems)

    quote = quotes.lock_quote(checkout.quote_id, reference=str(checkout.public_id), now=now)  # raises quote_expired / already_locked
    quote_items = {qi.variant_id: qi for qi in quote.items.prefetch_related("components")}

    groups: dict[str, list] = {}
    for item in cart_items:
        groups.setdefault(item.variant.fulfillment_type, []).append(item)
    order_types = [t for t in (OrderType.DIRECT_INVENTORY, OrderType.PROXY_PURCHASE) if t in groups]

    weights = [sum((quote_items[i.variant_id].line_total_irr for i in groups[t]), Decimal(0)) for t in order_types]
    shipping_shares = allocate(checkout.shipping_fee_irr, weights)

    due = now + timedelta(minutes=settings.ORDER_PAYMENT_WINDOW_MINUTES)
    # Once locked, the quote no longer expires; the customer has `ORDER_PAYMENT_WINDOW_MINUTES` to pay.
    orders: list[Order] = []
    for order_type, ship_share in zip(order_types, shipping_shares):
        lines = groups[order_type]
        order = Order(
            code=codes.next_code(), checkout=checkout, user=user, order_type=order_type, quote=quote,
            customer_name=checkout.customer_snapshot.get("name", ""), customer_phone=checkout.customer_snapshot.get("phone", ""),
            customer_email=checkout.customer_snapshot.get("email", ""), shipping_address=checkout.address_snapshot,
            shipping_method_code=checkout.shipping_method_code, shipping_label=checkout.shipping_label, shipping_snapshot=checkout.shipping_snapshot,
            display_currency=checkout.display_currency, display_rate=quote.display_rate, display_rate_source=quote.display_rate_source,
            items_subtotal_irr=Decimal(0), separate_charges_irr=Decimal(0), shipping_irr=ship_share, discount_irr=Decimal(0), total_irr=ship_share,
            has_estimated_costs=any(quote_items[i.variant_id].has_estimated_costs for i in lines) or checkout.shipping_value_status == "estimated",
            placed_at=now, payment_due_at=due,
        )  # fmt: skip
        rows = [snapshots.build_item(order, i.variant, quote_items[i.variant_id], i.quantity) for i in lines]
        order.items_subtotal_irr = sum((r.line_price_irr for r in rows), Decimal(0))
        order.separate_charges_irr = sum((r.line_separate_irr for r in rows), Decimal(0))
        order.discount_irr = sum((r.discount_unit_irr * r.quantity for r in rows), Decimal(0))
        order.total_irr = order.items_subtotal_irr + order.separate_charges_irr + ship_share
        order.save()
        for row in rows:
            row.save()
            if order_type == OrderType.PROXY_PURCHASE:
                ProcurementRecord.objects.create(order_item=row)
        OrderStatusHistory.objects.create(order=order, from_status="", to_status=OrderStatus.PENDING_PAYMENT, actor_type=ActorType.CUSTOMER, actor=user, reason="order placed", created_at=now)
        if order_type == OrderType.DIRECT_INVENTORY:
            # Atomic conditional UPDATEs: concurrent buyers cannot oversell. Raises out_of_stock -> everything rolls back.
            stock.reserve([(r.variant, r.quantity) for r in rows], holder_type="order", holder_id=str(order.public_id), expires_at=due)
        orders.append(order)
        audit.record("order.created", actor=user, target=order, code=order.code, total_irr=str(order.total_irr), order_type=order_type)

    if sum((o.total_irr for o in orders), Decimal(0)) != checkout.total_irr:
        raise AssertionError("order totals do not add up to the checkout total")  # defensive: rolls everything back

    checkout.status, checkout.completed_at = CheckoutStatus.COMPLETED, now
    checkout.save(update_fields=["status", "completed_at"])
    carts.mark_converted(cart)
    return orders
