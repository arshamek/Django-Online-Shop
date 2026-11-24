"""
Checkout, orders and their immutable price snapshots (docs/database/DATABASE_DESIGN.md 2.10).

* A `Checkout` freezes one authoritative `PriceQuote`, the delivery address, the delivery method and the totals the
  customer was shown. Creating orders from it is idempotent and transactional.
* A checkout produces one `Order` per fulfilment type (DIRECT_INVENTORY / PROXY_PURCHASE): the two have different
  state machines, suppliers and delivery times, so they are never mixed in one order.
* `OrderItem` rows are immutable snapshots: product title, variant, quantity, source price, currency, exchange rate,
  accepted selling price, discount, fees, shipping and totals. Nothing here is ever recomputed from today's catalogue.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from .state import OrderStatus, OrderType

IRR = {"max_digits": 18, "decimal_places": 0}


def irr(**kw):
    return models.DecimalField(**IRR, validators=[MinValueValidator(Decimal("0"))], **kw)


class CheckoutStatus(models.TextChoices):
    OPEN = "open", "Open"
    COMPLETED = "completed", "Orders created"
    EXPIRED = "expired", "Expired"
    INVALIDATED = "invalidated", "Invalidated (cart changed)"


class Checkout(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="checkouts")
    cart = models.ForeignKey("cart.Cart", on_delete=models.PROTECT, related_name="checkouts")
    cart_version = models.PositiveIntegerField()
    cart_fingerprint = models.CharField(max_length=64)
    status = models.CharField(max_length=12, choices=CheckoutStatus.choices, default=CheckoutStatus.OPEN)
    quote = models.ForeignKey("pricing.PriceQuote", on_delete=models.PROTECT, related_name="checkouts")
    address = models.ForeignKey("accounts.Address", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    address_snapshot = models.JSONField(default=dict)
    customer_snapshot = models.JSONField(default=dict)
    shipping_method_code = models.CharField(max_length=40)
    shipping_label = models.CharField(max_length=120, blank=True)
    shipping_fee_irr = irr()
    shipping_value_status = models.CharField(max_length=10, blank=True)
    display_currency = models.CharField(max_length=3)
    merchandise_irr = irr()
    separate_charges_irr = irr(default=Decimal("0"))
    customs_estimate_irr = irr(default=Decimal("0"))
    discount_irr = irr(default=Decimal("0"))
    total_irr = irr()
    has_estimated_costs = models.BooleanField(default=False)
    warnings = models.JSONField(default=list, blank=True)
    summary = models.JSONField(default=dict, help_text="Display lines as shown to the customer.")
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)

    _MUTABLE = {"status", "completed_at"}

    class Meta:
        db_table = "orders_checkout"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "expires_at"], name="checkout_status_exp_idx")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(total_irr=models.F("merchandise_irr") + models.F("separate_charges_irr") + models.F("shipping_fee_irr")),
                name="checkout_total_adds_up",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            fields = set(kwargs.get("update_fields") or ())
            if not fields or not fields <= self._MUTABLE:
                raise RuntimeError("A checkout is an immutable snapshot (only status fields may change).")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Checkouts cannot be deleted.")


class Order(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    code = models.CharField(max_length=20, unique=True, help_text="Customer-facing tracking code, MRL-######")
    checkout = models.ForeignKey(Checkout, on_delete=models.PROTECT, related_name="orders")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    order_type = models.CharField(max_length=20, choices=OrderType.choices)
    status = models.CharField(max_length=20, choices=OrderStatus.choices, default=OrderStatus.PENDING_PAYMENT)
    quote = models.ForeignKey("pricing.PriceQuote", on_delete=models.PROTECT, related_name="orders")

    customer_name = models.CharField(max_length=150)
    customer_phone = models.CharField(max_length=20)
    customer_email = models.CharField(max_length=254, blank=True)
    shipping_address = models.JSONField(default=dict)
    shipping_method_code = models.CharField(max_length=40)
    shipping_label = models.CharField(max_length=120, blank=True)

    display_currency = models.CharField(max_length=3)
    display_rate = models.DecimalField(max_digits=24, decimal_places=10, null=True, blank=True)
    display_rate_source = models.CharField(max_length=40, blank=True)

    items_subtotal_irr = irr()
    separate_charges_irr = irr(default=Decimal("0"))
    shipping_irr = irr(default=Decimal("0"))
    discount_irr = irr(default=Decimal("0"), help_text="Informational: list price minus accepted price.")
    total_irr = irr()
    refunded_irr = irr(default=Decimal("0"))
    has_estimated_costs = models.BooleanField(default=False)

    placed_at = models.DateTimeField(default=timezone.now)
    payment_due_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.CharField(max_length=300, blank=True)
    tracking_carrier = models.CharField(max_length=60, blank=True)
    tracking_number = models.CharField(max_length=80, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    _MUTABLE = {
        "status", "paid_at", "shipped_at", "delivered_at", "cancelled_at", "cancel_reason",
        "tracking_carrier", "tracking_number", "refunded_irr", "updated_at",
    }  # fmt: skip

    class Meta:
        db_table = "orders_order"
        ordering = ["-placed_at", "-id"]
        indexes = [
            models.Index(fields=["user", "-placed_at"], name="order_user_placed_idx"),
            models.Index(fields=["status", "payment_due_at"], name="order_status_due_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(total_irr=models.F("items_subtotal_irr") + models.F("separate_charges_irr") + models.F("shipping_irr")),
                name="order_total_adds_up",
            ),
            models.CheckConstraint(condition=models.Q(refunded_irr__lte=models.F("total_irr")), name="order_refund_le_total"),
        ]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        if self.pk is not None:
            fields = set(kwargs.get("update_fields") or ())
            if not fields or not fields <= self._MUTABLE:
                raise RuntimeError("Order money and snapshots are immutable; only lifecycle fields may change.")
            if "status" in fields and not getattr(self, "_status_write_allowed", False):
                raise RuntimeError("Order.status can only be changed through services.transitions.transition().")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Orders cannot be deleted.")


class OrderItem(models.Model):
    """Immutable snapshot of one purchased line (see module docstring)."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="items")
    variant = models.ForeignKey("catalog.ProductVariant", on_delete=models.PROTECT, related_name="+")
    quote_item = models.ForeignKey("pricing.PriceQuoteItem", on_delete=models.PROTECT, related_name="+")

    # what was bought
    product_name = models.CharField(max_length=255)
    brand_name = models.CharField(max_length=160, blank=True)
    category_slug = models.CharField(max_length=80, blank=True, help_text="Snapshot, used by return eligibility rules.")
    sku = models.CharField(max_length=80)
    size_code = models.CharField(max_length=40, blank=True)
    size_name = models.CharField(max_length=120, blank=True)
    color_code = models.CharField(max_length=40, blank=True)
    color_name = models.CharField(max_length=120, blank=True)
    variant_options = models.JSONField(default=dict)
    image_url = models.CharField(max_length=500, blank=True)
    quantity = models.PositiveIntegerField()

    # fulfilment + external source (proxy)
    fulfillment_type = models.CharField(max_length=20, choices=OrderType.choices)
    source = models.ForeignKey("product_sources.ProductSource", on_delete=models.PROTECT, related_name="+")
    source_code = models.CharField(max_length=40)
    source_product_id = models.CharField(max_length=120, blank=True)
    source_variant_id = models.CharField(max_length=120, blank=True)
    source_product_url = models.CharField(max_length=500, blank=True)
    purchase_requirements = models.JSONField(default=dict, blank=True)
    expected_days_min = models.PositiveSmallIntegerField(null=True, blank=True)
    expected_days_max = models.PositiveSmallIntegerField(null=True, blank=True)
    expected_days_unit = models.CharField(max_length=20, blank=True)

    # price snapshot (IRR unless a currency is named)
    original_amount = models.DecimalField(max_digits=18, decimal_places=4)
    original_currency = models.CharField(max_length=3)
    exchange_rate = models.DecimalField(max_digits=24, decimal_places=10, help_text="IRR per 1 unit of original_currency")
    exchange_rate_source = models.CharField(max_length=40)
    exchange_rate_at = models.DateTimeField(null=True, blank=True)
    exchange_rate_is_manual = models.BooleanField(default=False)
    list_unit_irr = irr()
    unit_price_irr = irr()  # accepted selling price
    discount_unit_irr = irr(default=Decimal("0"))
    fees_unit_irr = irr(default=Decimal("0"))  # purchase + service fees inside the price
    freight_unit_irr = irr(default=Decimal("0"))  # international shipping component
    customs_unit_irr = irr(default=Decimal("0"))  # customs / duties component (estimate flag below)
    unit_separate_irr = irr(default=Decimal("0"))  # charged on top of the price
    line_price_irr = irr()
    line_separate_irr = irr(default=Decimal("0"))
    line_total_irr = irr()
    has_estimated_costs = models.BooleanField(default=False)
    pricing_rule_version = models.PositiveIntegerField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "orders_item"
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="oitem_qty_positive"),
            models.CheckConstraint(
                condition=models.Q(line_price_irr=models.F("unit_price_irr") * models.F("quantity")), name="oitem_line_eq_unit_qty"
            ),
            models.CheckConstraint(
                condition=models.Q(line_total_irr=models.F("line_price_irr") + models.F("line_separate_irr")), name="oitem_total_adds_up"
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("Order items are immutable snapshots.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Order items cannot be deleted.")


class ActorType(models.TextChoices):
    SYSTEM = "system", "System"
    CUSTOMER = "customer", "Customer"
    STAFF = "staff", "Staff"
    PAYMENT = "payment", "Payment provider"


class OrderStatusHistory(models.Model):
    """Append-only record of every transition (the creation row has from_status = '')."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="history")
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, choices=OrderStatus.choices)
    actor_type = models.CharField(max_length=10, choices=ActorType.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reason = models.CharField(max_length=300, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "orders_status_history"
        ordering = ["id"]
        indexes = [models.Index(fields=["order", "id"], name="ohist_order_idx")]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("Order status history is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Order status history cannot be deleted.")


class ProcurementStatus(models.TextChoices):
    AWAITING_PAYMENT = "awaiting_payment", "Awaiting payment"
    QUEUED = "queued", "Queued for purchase"
    PURCHASED = "purchased", "Purchased"
    SOURCE_UNAVAILABLE = "source_unavailable", "Source unavailable / price changed"
    CANCELLED = "cancelled", "Cancelled"


class ProcurementRecord(models.Model):
    """Staff-side record of buying a PROXY item at the source store (the customer-facing snapshot is OrderItem)."""

    order_item = models.OneToOneField(OrderItem, on_delete=models.PROTECT, related_name="procurement")
    status = models.CharField(max_length=20, choices=ProcurementStatus.choices, default=ProcurementStatus.AWAITING_PAYMENT)
    source_order_ref = models.CharField(max_length=120, blank=True)
    actual_cost_amount = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    actual_cost_currency = models.CharField(max_length=3, blank=True)
    purchased_at = models.DateTimeField(null=True, blank=True)
    buyer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    notes = models.CharField(max_length=500, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "orders_procurement"


class OrderCodeCounter(models.Model):
    """Single row, locked while a code is issued, so codes are sequential and never collide."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    last_value = models.PositiveBigIntegerField(default=100000)

    class Meta:
        db_table = "orders_code_counter"


class IdempotencyRecord(models.Model):
    """Same (user, scope, key) + same body => the stored response; different body => 409."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    scope = models.CharField(max_length=40)
    key = models.CharField(max_length=80)
    request_hash = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField(default=0)
    response_body = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "orders_idempotency"
        constraints = [models.UniqueConstraint(fields=["user", "scope", "key"], name="idem_user_scope_key_uniq")]


class RefundKind(models.TextChoices):
    CANCELLATION = "cancellation", "Order cancelled after payment"
    RETURN = "return", "Return of delivered goods"
    LATE_PAYMENT = "late_payment", "Payment arrived after the order was cancelled"
    STAFF = "staff", "Staff-initiated"


class RefundStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    COMPLETED = "completed", "Completed (money returned)"
    FAILED = "failed", "Failed"


OPEN_REFUND = (RefundStatus.REQUESTED, RefundStatus.APPROVED)
COUNTING_REFUND = (RefundStatus.REQUESTED, RefundStatus.APPROVED, RefundStatus.COMPLETED)


class Refund(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="refunds")
    kind = models.CharField(max_length=14, choices=RefundKind.choices)
    status = models.CharField(max_length=10, choices=RefundStatus.choices, default=RefundStatus.REQUESTED)
    amount_irr = models.DecimalField(max_digits=18, decimal_places=0, validators=[MinValueValidator(Decimal("1"))])
    reason = models.CharField(max_length=500, blank=True)
    resume_status = models.CharField(max_length=20, choices=OrderStatus.choices, help_text="Order status to return to if rejected.")
    requested_by_type = models.CharField(max_length=10, choices=ActorType.choices)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=500, blank=True)
    provider_ref = models.CharField(max_length=120, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "orders_refund"
        ordering = ["-created_at", "-id"]
        constraints = [models.CheckConstraint(condition=models.Q(amount_irr__gt=0), name="refund_amount_positive")]


class RefundItem(models.Model):
    refund = models.ForeignKey(Refund, on_delete=models.CASCADE, related_name="items")
    order_item = models.ForeignKey(OrderItem, on_delete=models.PROTECT, related_name="+")
    quantity = models.PositiveIntegerField()

    class Meta:
        db_table = "orders_refund_item"
        constraints = [
            models.UniqueConstraint(fields=["refund", "order_item"], name="refunditem_refund_item_uniq"),
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="refunditem_qty_positive"),
        ]
