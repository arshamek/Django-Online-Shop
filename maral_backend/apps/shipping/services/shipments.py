"""
Shipments: tracking numbers, the delivery status machine, shipment history, and the link to the order.

* One order normally has one shipment (the model allows several parcels). The order's own tracking fields mirror the
  latest shipment so the Phase 7 API keeps working.
* A method flagged `requires_tracking` cannot leave PENDING without a tracking number.
* Carrier-reported facts (tracking events, estimated delivery) are stored as reported; nothing is ever inferred or
  invented. Events with an external id are idempotent.
"""

import re
from urllib.parse import quote as url_quote

import structlog
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit import services as audit
from apps.common.exceptions import BusinessRuleViolation, Conflict

from ..models import EventSource, Shipment, ShipmentEvent, ShipmentStatus, ShippingMethod
from ..providers import registry
from ..providers.base import ShippingProviderError

logger = structlog.get_logger("maral.shipping")
S = ShipmentStatus

TRANSITIONS: dict[str, frozenset[str]] = {
    S.PENDING: frozenset({S.IN_TRANSIT, S.CANCELLED, S.EXCEPTION}),
    S.IN_TRANSIT: frozenset({S.OUT_FOR_DELIVERY, S.DELIVERED, S.FAILED_ATTEMPT, S.RETURNED, S.EXCEPTION}),
    S.OUT_FOR_DELIVERY: frozenset({S.DELIVERED, S.FAILED_ATTEMPT, S.EXCEPTION}),
    S.FAILED_ATTEMPT: frozenset({S.IN_TRANSIT, S.OUT_FOR_DELIVERY, S.DELIVERED, S.RETURNED, S.EXCEPTION}),
    S.EXCEPTION: frozenset({S.IN_TRANSIT, S.OUT_FOR_DELIVERY, S.DELIVERED, S.RETURNED, S.CANCELLED}),
    S.DELIVERED: frozenset(),
    S.RETURNED: frozenset(),
    S.CANCELLED: frozenset(),
}
TERMINAL = frozenset(s for s, nxt in TRANSITIONS.items() if not nxt)
NEEDS_TRACKING = frozenset({S.IN_TRANSIT, S.OUT_FOR_DELIVERY, S.DELIVERED, S.FAILED_ATTEMPT, S.RETURNED})
DISPATCHED = frozenset({S.IN_TRANSIT, S.OUT_FOR_DELIVERY})
_TRACKING = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/\-]{2,79}$")
SHIPPABLE_ORDER_STATUSES = ("PROCESSING", "READY_TO_SHIP", "SHIPPED")


# ------------------------------------------------------------------------------------------------ helpers
def method_for(order) -> ShippingMethod | None:
    return ShippingMethod.objects.filter(code=order.shipping_method_code).first()


def clean_tracking_number(value: str) -> str:
    number = (value or "").strip()
    if number and not _TRACKING.match(number):
        raise BusinessRuleViolation("The tracking number has an invalid format.", code="invalid_tracking_number")
    return number


def tracking_url(method: ShippingMethod | None, number: str) -> str:
    """https URL from the method's template; '' when there is no template or no number. Never built from client input."""
    template = (method.tracking_url_template if method else "") or ""
    if not number or not template.startswith("https://") or "{tracking_number}" not in template:
        return ""
    return template.replace("{tracking_number}", url_quote(number, safe=""))


def assert_can_ship(order, tracking_number: str) -> None:
    """Called by the order state machine before SHIPPED: a method that requires tracking cannot ship without it."""
    method = method_for(order)
    if method is not None and method.requires_tracking and not (tracking_number or "").strip():
        raise BusinessRuleViolation("A tracking number is required to ship this order.", code="tracking_required")


def _event(
    shipment, status, *, source, actor=None, description="", location="", occurred_at=None, external_id="", raw=None
):
    return ShipmentEvent.objects.create(
        shipment=shipment, status=status, description=description[:300], location=location[:120], occurred_at=occurred_at or timezone.now(),
        source=source, actor=actor if getattr(actor, "is_authenticated", False) else None, external_id=external_id[:120], raw=raw or {},
    )  # fmt: skip


def _mirror_to_order(shipment: Shipment) -> None:
    from apps.orders.models import Order

    order = Order.objects.get(pk=shipment.order_id)
    order.tracking_carrier, order.tracking_number = shipment.carrier[:60], shipment.tracking_number[:80]
    order.save(update_fields=["tracking_carrier", "tracking_number", "updated_at"])


# ------------------------------------------------------------------------------------------------ create / tracking
def create_shipment(
    order, *, actor=None, carrier: str = "", tracking_number: str = "", use_provider: bool = False
) -> Shipment:
    if order.status not in SHIPPABLE_ORDER_STATUSES:
        raise BusinessRuleViolation("This order is not at a stage where it can be shipped.", code="order_not_shippable")
    method = method_for(order)
    number = clean_tracking_number(tracking_number)
    with transaction.atomic():
        shipment = Shipment.objects.create(
            order=order, method_code=order.shipping_method_code, provider_key=(method.provider_key if method else "table"),
            carrier=(carrier or (method.carrier_code if method else ""))[:60], tracking_number=number, tracking_url=tracking_url(method, number),
            created_by=actor if getattr(actor, "is_authenticated", False) else None,
        )  # fmt: skip
        _event(
            shipment,
            S.PENDING,
            source=EventSource.STAFF if actor else EventSource.SYSTEM,
            actor=actor,
            description="Shipment created",
        )
    if use_provider:
        adapter = registry.get(shipment.provider_key)
        if adapter is None or not adapter.capabilities.creates_shipments:
            raise BusinessRuleViolation(
                "This method's provider cannot create shipments; enter the tracking number manually.",
                code="provider_cannot_create_shipments",
            )
        try:
            result = adapter.create_shipment(method, shipment)
        except ShippingProviderError:
            raise Conflict(
                "The carrier could not be reached; the shipment exists without a tracking number.",
                code="carrier_unreachable",
            ) from None
        shipment.carrier_reference = result.carrier_reference[:120]
        shipment.carrier_estimated_delivery_at = result.estimated_delivery_at
        shipment.save(update_fields=["carrier_reference", "carrier_estimated_delivery_at", "updated_at"])
        set_tracking(shipment, carrier=shipment.carrier, tracking_number=result.tracking_number, actor=actor)
    elif number:
        _mirror_to_order(shipment)
    audit.record(
        "shipment.created", actor=actor, target=shipment, order=order.code, tracking=bool(shipment.tracking_number)
    )
    shipment.refresh_from_db()
    return shipment


def set_tracking(shipment: Shipment, *, carrier: str, tracking_number: str, actor=None) -> Shipment:
    number = clean_tracking_number(tracking_number)
    if not number:
        raise BusinessRuleViolation("A tracking number is required.", code="tracking_required")
    with transaction.atomic():
        locked = Shipment.objects.select_for_update().get(pk=shipment.pk)
        if locked.status == S.CANCELLED:
            raise Conflict("A cancelled shipment cannot be edited.", code="shipment_cancelled")
        changed = (locked.carrier, locked.tracking_number) != (carrier[:60], number)
        locked.carrier, locked.tracking_number = carrier[:60], number
        locked.tracking_url = tracking_url(method_for(locked.order), number)
        locked.save(update_fields=["carrier", "tracking_number", "tracking_url", "updated_at"])
        if changed:
            _event(
                locked,
                locked.status,
                source=EventSource.STAFF if actor else EventSource.SYSTEM,
                actor=actor,
                description="Tracking number set or changed",
            )
        _mirror_to_order(locked)
    audit.record("shipment.tracking_set", actor=actor, target=locked, carrier=carrier[:60])
    return locked


# ------------------------------------------------------------------------------------------------ status machine
def apply_status(
    shipment: Shipment, status: str, *, actor=None, source: str = EventSource.STAFF, description: str = "", location: str = "",
    occurred_at=None, external_id: str = "", raw=None, sync_order: bool = True, strict: bool = True,
):  # fmt: skip
    """Move a shipment along its machine and record the event. Returns the event, or None for an idempotent repeat.

    strict=False (carrier sync): an event that does not fit the machine is kept in the history but does not move the status.
    """
    if status not in S.values:
        raise BusinessRuleViolation("Unknown shipment status.", code="invalid_shipment_status")
    with transaction.atomic():
        locked = Shipment.objects.select_for_update().get(pk=shipment.pk)
        if external_id and ShipmentEvent.objects.filter(shipment=locked, external_id=external_id).exists():
            return None  # carrier events are idempotent
        same = status == locked.status
        if same and not external_id:
            return None  # a staff repeat is a no-op; a carrier scan at the same status is still history (and idempotent by id)
        allowed = status in TRANSITIONS[locked.status]
        if not allowed and strict and not same:
            raise Conflict(
                f"A shipment cannot move from {locked.status} to {status}.", code="invalid_shipment_transition"
            )
        if allowed and status in NEEDS_TRACKING:
            method = method_for(locked.order)
            if method is not None and method.requires_tracking and not locked.tracking_number:
                raise BusinessRuleViolation(
                    "A tracking number is required before this shipment can move on.", code="tracking_required"
                )
        when = occurred_at or timezone.now()
        if allowed:
            locked.status = status
            if status == S.IN_TRANSIT and locked.shipped_at is None:
                locked.shipped_at = when
            if status == S.DELIVERED:
                locked.delivered_at = when
            locked.save(update_fields=["status", "shipped_at", "delivered_at", "updated_at"])
        try:
            with transaction.atomic():
                event = _event(
                    locked,
                    status,
                    source=source,
                    actor=actor,
                    description=description,
                    location=location,
                    occurred_at=when,
                    external_id=external_id,
                    raw=raw,
                )
        except IntegrityError:
            return None
    if allowed and sync_order:
        _sync_order(locked, actor=actor, source=source)
    if allowed:
        audit.record("shipment.status", actor=actor, target=locked, status=status, source=source)
    return event


def _sync_order(shipment: Shipment, *, actor, source) -> None:
    from apps.orders.models import ActorType, Order
    from apps.orders.services import transitions
    from apps.orders.state import OrderStatus

    order = Order.objects.get(pk=shipment.order_id)
    actor_type = (
        ActorType.STAFF if actor is not None and getattr(actor, "is_authenticated", False) else ActorType.SYSTEM
    )
    meta = {
        "shipment": str(shipment.public_id),
        "tracking_carrier": shipment.carrier,
        "tracking_number": shipment.tracking_number,
    }
    if shipment.status in DISPATCHED and order.status == OrderStatus.READY_TO_SHIP:
        transitions.transition(
            order, OrderStatus.SHIPPED, actor_type=actor_type, actor=actor, reason="shipment dispatched", metadata=meta
        )
    elif shipment.status == S.DELIVERED and order.status == OrderStatus.SHIPPED:
        pending = order.shipments.exclude(status__in=(S.DELIVERED, S.CANCELLED)).exists()
        if not pending:
            transitions.transition(
                order,
                OrderStatus.DELIVERED,
                actor_type=actor_type,
                actor=actor,
                reason=f"shipment delivered ({source})",
                metadata=meta,
            )


# ------------------------------------------------------------------------------------------------ hooks from the order machine
def on_order_shipped(order, actor_type: str = "system", actor=None) -> None:
    """The order was moved to SHIPPED by staff: make sure a shipment reflects it (never a second one)."""
    active = order.shipments.exclude(status__in=(S.CANCELLED, S.RETURNED)).order_by("id").first()
    method = method_for(order)
    if active is None:
        shipment = Shipment.objects.create(
            order=order, method_code=order.shipping_method_code, provider_key=(method.provider_key if method else "table"),
            carrier=order.tracking_carrier, tracking_number=order.tracking_number, tracking_url=tracking_url(method, order.tracking_number),
            status=S.IN_TRANSIT, shipped_at=order.shipped_at or timezone.now(),
            created_by=actor if getattr(actor, "is_authenticated", False) else None,
        )  # fmt: skip
        _event(
            shipment,
            S.IN_TRANSIT,
            source=EventSource.STAFF if actor else EventSource.SYSTEM,
            actor=actor,
            description="Order marked as shipped",
        )
        return
    if active.status == S.PENDING:
        if order.tracking_number and not active.tracking_number:
            active.carrier, active.tracking_number = order.tracking_carrier, order.tracking_number
            active.tracking_url = tracking_url(method, order.tracking_number)
            active.save(update_fields=["carrier", "tracking_number", "tracking_url", "updated_at"])
        apply_status(active, S.IN_TRANSIT, actor=actor, description="Order marked as shipped", sync_order=False)


def on_order_delivered(order, actor=None) -> None:
    for shipment in order.shipments.exclude(status__in=(S.DELIVERED, S.CANCELLED, S.RETURNED)):
        if S.DELIVERED in TRANSITIONS[shipment.status] or shipment.status == S.PENDING:
            if shipment.status == S.PENDING:  # delivered without ever being dispatched through the shipment API
                apply_status(
                    shipment,
                    S.IN_TRANSIT,
                    actor=actor,
                    description="Order marked as delivered",
                    sync_order=False,
                    strict=False,
                )
            apply_status(
                shipment,
                S.DELIVERED,
                actor=actor,
                description="Order marked as delivered",
                sync_order=False,
                strict=False,
            )


# ------------------------------------------------------------------------------------------------ carrier sync
def sync_from_provider(shipment: Shipment) -> int:
    """Pull the carrier's scans and apply the new ones in time order. Returns how many events were new."""
    adapter = registry.get(shipment.provider_key)
    if adapter is None or not adapter.capabilities.supports_tracking_sync:
        raise BusinessRuleViolation("This shipment's provider has no tracking API.", code="tracking_sync_not_supported")
    if not shipment.tracking_number:
        raise BusinessRuleViolation("The shipment has no tracking number yet.", code="tracking_required")
    try:
        result = adapter.fetch_tracking(shipment)
    except ShippingProviderError:
        raise Conflict("The carrier could not be reached.", code="carrier_unreachable") from None
    applied = 0
    for ev in sorted(result.events, key=lambda e: e.occurred_at or timezone.now()):
        if ev.status not in S.values:
            continue
        if apply_status(shipment, ev.status, source=EventSource.CARRIER, description=ev.description, location=ev.location, occurred_at=ev.occurred_at,
                        external_id=ev.external_id, raw={"carrier": shipment.carrier}, strict=False) is not None:  # fmt: skip
            applied += 1
        shipment.refresh_from_db()
    Shipment.objects.filter(pk=shipment.pk).update(
        last_synced_at=timezone.now(),
        **({"carrier_estimated_delivery_at": result.estimated_delivery_at} if result.estimated_delivery_at else {}),
    )
    return applied


def sync_all(limit: int = 200) -> dict:
    stats = {"synced": 0, "events": 0, "errors": 0}
    open_shipments = (
        Shipment.objects.exclude(status__in=TERMINAL)
        .exclude(tracking_number="")
        .order_by("last_synced_at", "id")[:limit]
    )
    for shipment in open_shipments:
        adapter = registry.get(shipment.provider_key)
        if adapter is None or not adapter.capabilities.supports_tracking_sync:
            continue
        try:
            stats["events"] += sync_from_provider(shipment)
            stats["synced"] += 1
        except (BusinessRuleViolation, Conflict):
            stats["errors"] += 1
    return stats
