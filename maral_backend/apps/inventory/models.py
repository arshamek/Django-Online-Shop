"""
Internal stock (DIRECT_INVENTORY). docs/database/DATABASE_DESIGN.md 2.8.

* `InventoryItem` holds `on_hand` / `reserved` / `safety_stock` per (variant, warehouse); `0 <= reserved <= on_hand`.
* Reserving is ONE conditional UPDATE (`on_hand >= reserved + safety + qty`), never read-then-write, so two buyers
  can never both take the last unit.
* Every change writes an append-only `InventoryMovement`; (item, type, reference) is unique so a retried order
  event cannot move stock twice.
"""

import uuid

from django.db import models
from django.utils import timezone


class Warehouse(models.Model):
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        db_table = "inventory_warehouse"
        constraints = [
            models.UniqueConstraint(fields=["is_default"], condition=models.Q(is_default=True), name="wh_one_default")
        ]

    def __str__(self):
        return self.code


class InventoryItem(models.Model):
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="inventory_items")
    warehouse = models.ForeignKey(Warehouse, on_delete=models.PROTECT, related_name="items")
    on_hand = models.PositiveIntegerField(default=0)
    reserved = models.PositiveIntegerField(default=0)
    safety_stock = models.PositiveIntegerField(default=0, help_text="Never sold; kept back for damage / mis-picks.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_item"
        constraints = [
            models.UniqueConstraint(fields=["variant", "warehouse"], name="invitem_variant_wh_uniq"),
            models.CheckConstraint(condition=models.Q(reserved__lte=models.F("on_hand")), name="invitem_reserved_le_onhand"),
        ]

    @property
    def sellable(self) -> int:
        return max(0, self.on_hand - self.reserved - self.safety_stock)


class MovementType(models.TextChoices):
    RECEIPT = "receipt", "Receipt (stock in)"
    RESERVE = "reserve", "Reserve for an order"
    RELEASE = "release", "Release a reservation"
    COMMIT = "commit", "Commit a reservation (sold)"
    RETURN = "return", "Return to stock"
    ADJUST = "adjust", "Manual adjustment"


class InventoryMovement(models.Model):
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=10, choices=MovementType.choices)
    on_hand_delta = models.IntegerField(default=0)
    reserved_delta = models.IntegerField(default=0)
    reference_type = models.CharField(max_length=20, blank=True)
    reference_id = models.CharField(max_length=64, blank=True)
    reason = models.CharField(max_length=200, blank=True)
    actor = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "inventory_movement"
        ordering = ["-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "movement_type", "reference_type", "reference_id"],
                condition=~models.Q(reference_id=""),
                name="invmove_idempotent_ref",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("Inventory movements are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Inventory movements cannot be deleted.")


class ReservationStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    COMMITTED = "committed", "Committed (sold)"
    RELEASED = "released", "Released"
    EXPIRED = "expired", "Expired"
    RETURNED = "returned", "Returned to stock after commit"


class InventoryReservation(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="reservations")
    holder_type = models.CharField(max_length=20, default="order")
    holder_id = models.CharField(max_length=64)
    quantity = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=ReservationStatus.choices, default=ReservationStatus.ACTIVE)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_reservation"
        indexes = [
            models.Index(fields=["status", "expires_at"], name="invres_status_exp_idx"),
            models.Index(fields=["holder_type", "holder_id"], name="invres_holder_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="invres_qty_positive"),
            models.UniqueConstraint(
                fields=["holder_type", "holder_id", "item"], name="invres_holder_item_uniq"
            ),
        ]
