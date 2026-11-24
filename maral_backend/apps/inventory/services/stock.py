"""Stock operations. All mutations are atomic, conditional and recorded as movements."""

from collections import defaultdict
from datetime import datetime

import structlog
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.common.exceptions import BusinessRuleViolation, Conflict

from ..models import (
    InventoryItem,
    InventoryMovement,
    InventoryReservation,
    MovementType,
    ReservationStatus,
    Warehouse,
)

logger = structlog.get_logger("maral.inventory")


class InsufficientStock(Conflict):
    default_code = "out_of_stock"
    default_detail = "Not enough stock."

    def __init__(self, detail=None, code=None, variant_ids=None):
        super().__init__(detail=detail, code=code)
        self.variant_ids = list(variant_ids or [])


# ----------------------------------------------------------------------------------------------- reads
def sellable_quantities(variant_ids) -> dict[int, int]:
    """variant_id -> units that can be sold now (0 when there is no inventory row)."""
    totals: dict[int, int] = defaultdict(int)
    rows = InventoryItem.objects.filter(variant_id__in=list(variant_ids), warehouse__is_active=True)
    for item in rows:
        totals[item.variant_id] += item.sellable
    return {vid: totals.get(vid, 0) for vid in variant_ids}


def default_warehouse() -> Warehouse:
    wh = Warehouse.objects.filter(is_default=True, is_active=True).first()
    if wh is None:
        wh, _ = Warehouse.objects.get_or_create(
            code="main", defaults={"name": "Main warehouse", "is_default": not Warehouse.objects.filter(is_default=True).exists()}
        )
    return wh


# ----------------------------------------------------------------------------------------------- movements
def _move(item, kind, *, on_hand=0, reserved=0, ref_type="", ref_id="", reason="", actor=None):
    """Writes the movement first: the idempotency constraint rejects a replay before any counter changes."""
    try:
        with transaction.atomic():
            InventoryMovement.objects.create(
                item=item,
                movement_type=kind,
                on_hand_delta=on_hand,
                reserved_delta=reserved,
                reference_type=ref_type,
                reference_id=ref_id,
                reason=reason[:200],
                actor=actor,
            )
    except IntegrityError:
        return False
    return True


def receive(variant, quantity: int, *, warehouse=None, reason="receipt", actor=None, reference=("", "")) -> InventoryItem:
    if quantity <= 0:
        raise BusinessRuleViolation("Quantity must be positive.", code="invalid_quantity")
    warehouse = warehouse or default_warehouse()
    with transaction.atomic():
        item, _ = InventoryItem.objects.get_or_create(variant=variant, warehouse=warehouse)
        item = InventoryItem.objects.select_for_update().get(pk=item.pk)
        if _move(item, MovementType.RECEIPT, on_hand=quantity, ref_type=reference[0], ref_id=reference[1], reason=reason, actor=actor):
            InventoryItem.objects.filter(pk=item.pk).update(on_hand=F("on_hand") + quantity)
        item.refresh_from_db()
    sync_availability([variant.pk])
    return item


def adjust(item: InventoryItem, new_on_hand: int, *, reason: str, actor=None) -> InventoryItem:
    if not reason.strip():
        raise BusinessRuleViolation("A reason is required for a stock adjustment.", code="reason_required")
    with transaction.atomic():
        item = InventoryItem.objects.select_for_update().get(pk=item.pk)
        if new_on_hand < item.reserved:
            raise BusinessRuleViolation("On-hand cannot drop below the reserved quantity.", code="below_reserved")
        delta = new_on_hand - item.on_hand
        if delta:
            _move(item, MovementType.ADJUST, on_hand=delta, reason=reason, actor=actor)
            InventoryItem.objects.filter(pk=item.pk).update(on_hand=new_on_hand)
        item.refresh_from_db()
    sync_availability([item.variant_id])
    return item


# ----------------------------------------------------------------------------------------------- reservations
def reserve(lines, *, holder_type: str, holder_id: str, expires_at: datetime) -> list[InventoryReservation]:
    """
    `lines` = [(variant, quantity)]. All-or-nothing: raises InsufficientStock (listing every short variant) and,
    because callers run this inside their transaction, nothing stays reserved. Re-calling for the same holder is a
    no-op (idempotent).
    """
    holder_id = str(holder_id)
    created: list[InventoryReservation] = []
    short: list[int] = []
    for variant, quantity in lines:
        remaining = quantity
        if InventoryReservation.objects.filter(holder_type=holder_type, holder_id=holder_id, item__variant=variant).exists():
            continue
        items = list(
            InventoryItem.objects.filter(variant=variant, warehouse__is_active=True).order_by("-on_hand", "id")
        )
        for item in items:
            if remaining == 0:
                break
            take = min(remaining, item.sellable)
            if take <= 0:
                continue
            # The guard lives in the WHERE clause: concurrent buyers cannot oversell.
            updated = InventoryItem.objects.filter(
                pk=item.pk, on_hand__gte=F("reserved") + F("safety_stock") + take
            ).update(reserved=F("reserved") + take)
            if not updated:
                fresh = InventoryItem.objects.get(pk=item.pk)
                take = min(remaining, fresh.sellable)
                if take <= 0 or not InventoryItem.objects.filter(
                    pk=item.pk, on_hand__gte=F("reserved") + F("safety_stock") + take
                ).update(reserved=F("reserved") + take):
                    continue
            reservation = InventoryReservation.objects.create(
                item=item, holder_type=holder_type, holder_id=holder_id, quantity=take, expires_at=expires_at
            )
            _move(item, MovementType.RESERVE, reserved=take, ref_type=holder_type, ref_id=f"{holder_id}:{variant.pk}", reason="order reservation")
            created.append(reservation)
            remaining -= take
        if remaining:
            short.append(variant.pk)
    if short:
        raise InsufficientStock("Not enough stock for one or more items.", variant_ids=short)
    return created


def _finish(holder_type, holder_id, from_status, apply):
    qs = InventoryReservation.objects.select_for_update().filter(
        holder_type=holder_type, holder_id=str(holder_id), status=from_status
    )
    touched = []
    for reservation in qs:
        apply(reservation)
        touched.append(reservation.item.variant_id)
    return touched


def release(holder_type: str, holder_id: str, *, status=ReservationStatus.RELEASED, reason="released") -> int:
    """Give back every ACTIVE reservation of a holder (cancellation, expiry)."""

    def apply(r):
        item = InventoryItem.objects.select_for_update().get(pk=r.item_id)
        if _move(item, MovementType.RELEASE, reserved=-r.quantity, ref_type=holder_type, ref_id=f"{r.holder_id}:{item.variant_id}", reason=reason):
            InventoryItem.objects.filter(pk=item.pk).update(reserved=F("reserved") - r.quantity)
        r.status = status
        r.save(update_fields=["status", "updated_at"])

    with transaction.atomic():
        touched = _finish(holder_type, holder_id, ReservationStatus.ACTIVE, apply)
    sync_availability(touched)
    return len(touched)


def commit(holder_type: str, holder_id: str) -> int:
    """Payment confirmed: the held units are now sold (on_hand and reserved both drop)."""

    def apply(r):
        item = InventoryItem.objects.select_for_update().get(pk=r.item_id)
        if _move(item, MovementType.COMMIT, on_hand=-r.quantity, reserved=-r.quantity, ref_type=holder_type, ref_id=f"{r.holder_id}:{item.variant_id}", reason="sold"):
            InventoryItem.objects.filter(pk=item.pk).update(on_hand=F("on_hand") - r.quantity, reserved=F("reserved") - r.quantity)
        r.status = ReservationStatus.COMMITTED
        r.save(update_fields=["status", "updated_at"])

    with transaction.atomic():
        touched = _finish(holder_type, holder_id, ReservationStatus.ACTIVE, apply)
    sync_availability(touched)
    return len(touched)


def return_committed(holder_type: str, holder_id: str, *, reason="order cancelled") -> int:
    """A sold-but-unshipped order was cancelled: units go back on the shelf."""

    def apply(r):
        item = InventoryItem.objects.select_for_update().get(pk=r.item_id)
        if _move(item, MovementType.RETURN, on_hand=r.quantity, ref_type=holder_type, ref_id=f"{r.holder_id}:{item.variant_id}", reason=reason):
            InventoryItem.objects.filter(pk=item.pk).update(on_hand=F("on_hand") + r.quantity)
        r.status = ReservationStatus.RETURNED
        r.save(update_fields=["status", "updated_at"])

    with transaction.atomic():
        touched = _finish(holder_type, holder_id, ReservationStatus.COMMITTED, apply)
    sync_availability(touched)
    return len(touched)


def expire_due(now=None) -> int:
    """Safety net: active reservations past their expiry (normally the orders task cancels the order first)."""
    now = now or timezone.now()
    holders = InventoryReservation.objects.filter(status=ReservationStatus.ACTIVE, expires_at__lte=now).values_list(
        "holder_type", "holder_id"
    ).distinct()
    total = 0
    for holder_type, holder_id in list(holders):
        total += release(holder_type, holder_id, status=ReservationStatus.EXPIRED, reason="reservation expired")
    return total


# ----------------------------------------------------------------------------------------------- catalogue sync
def sync_availability(variant_ids) -> None:
    """Catalogue availability of DIRECT variants follows internal stock (the catalogue never invents stock)."""
    from apps.catalog.models import AvailabilityStatus, FulfillmentType, ProductVariant, VariantAvailability

    ids = list(set(variant_ids))
    if not ids:
        return
    quantities = sellable_quantities(ids)
    for variant in ProductVariant.objects.filter(pk__in=ids, fulfillment_type=FulfillmentType.DIRECT_INVENTORY):
        status = AvailabilityStatus.IN_STOCK if quantities.get(variant.pk, 0) > 0 else AvailabilityStatus.OUT_OF_STOCK
        availability, created = VariantAvailability.objects.get_or_create(
            variant=variant, defaults={"status": status, "origin": "inventory"}
        )
        if not created and (availability.status != status or availability.origin != "inventory"):
            availability.status, availability.origin, availability.observed_at = status, "inventory", timezone.now()
            availability.save(update_fields=["status", "origin", "observed_at"])
