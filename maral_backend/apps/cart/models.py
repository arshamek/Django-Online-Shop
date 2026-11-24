"""
Shopping cart (docs/database/DATABASE_DESIGN.md 2.9). A cart belongs to exactly one owner: a customer, or an
anonymous browser identified by a server-issued random token (only its SHA-256 is stored).

A cart line stores NO price. Prices are always read from the server's catalogue when the cart is shown and from a
server-built price quote at checkout; `version` increases on every change so a checkout built from an older state
can be refused (`cart_changed`).
"""

import uuid

from django.conf import settings
from django.db import models


class CartStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    MERGED = "merged", "Merged into a customer cart"
    CONVERTED = "converted", "Converted into orders"
    ABANDONED = "abandoned", "Abandoned"


class Cart(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="carts")
    guest_token_hash = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=10, choices=CartStatus.choices, default=CartStatus.ACTIVE)
    version = models.PositiveIntegerField(default=1)
    expires_at = models.DateTimeField(null=True, blank=True, help_text="Guest carts only.")
    merged_into = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cart_cart"
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(user__isnull=False) & models.Q(guest_token_hash=""))
                | (models.Q(user__isnull=True) & ~models.Q(guest_token_hash="")),
                name="cart_exactly_one_owner",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="active", user__isnull=False),
                name="cart_one_active_per_user",
            ),
            models.UniqueConstraint(
                fields=["guest_token_hash"], condition=~models.Q(guest_token_hash=""), name="cart_guest_token_uniq"
            ),
        ]
        indexes = [models.Index(fields=["status", "expires_at"], name="cart_status_exp_idx")]


class CartItem(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)  # API `line_id`
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    quantity = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cart_item"
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["cart", "variant"], name="cartitem_cart_variant_uniq"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="cartitem_qty_positive"),
        ]
