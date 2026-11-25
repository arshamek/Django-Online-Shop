"""
Domestic delivery methods (what the checkout shows as «ارسال استاندارد / ارسال سریع»).

Fees are DATA, never code: nothing is seeded in production. International freight and customs for proxy items are a
different thing and live in the pricing engine's rules. `value_status` says whether the fee is a confirmed tariff or
an estimate; an unconfigured method is never priced as zero.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class FeeStatus(models.TextChoices):
    CONFIRMED = "confirmed", "Confirmed tariff"
    ESTIMATED = "estimated", "Estimated"


class MethodKind(models.TextChoices):
    DOMESTIC = "domestic", "Domestic"
    INTERNATIONAL = "international", "International"


class EstimateStatus(models.TextChoices):
    UNKNOWN = "unknown", "Unknown (nothing is promised)"
    ESTIMATED = "estimated", "Estimated range"
    CONFIRMED = "confirmed", "Confirmed by the carrier"


class ShippingMethod(models.Model):
    code = models.SlugField(max_length=40, unique=True)
    names = models.JSONField(default=dict, help_text='{"fa": "...", "en": "..."}')
    fee_irr = models.DecimalField(max_digits=18, decimal_places=0, validators=[MinValueValidator(Decimal("0"))])
    free_over_irr = models.DecimalField(
        max_digits=18,
        decimal_places=0,
        null=True,
        blank=True,
        help_text="Merchandise subtotal (IRR) from which this method is free. Empty = never free.",
    )
    value_status = models.CharField(max_length=10, choices=FeeStatus.choices, default=FeeStatus.ESTIMATED)
    countries = models.JSONField(default=list, blank=True, help_text="ISO country codes served. Empty = none.")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    # --- Phase 8. With ShippingRate rows the method is priced per zone; without them the legacy fee_irr/countries apply.
    kind = models.CharField(max_length=14, choices=MethodKind.choices, default=MethodKind.DOMESTIC)
    provider_key = models.SlugField(
        max_length=30, default="table", help_text="'table' = our own rates; otherwise a carrier adapter key"
    )
    carrier_code = models.SlugField(max_length=40, blank=True)
    descriptions = models.JSONField(
        default=dict, blank=True, help_text="Localized customer text, e.g. what the service is"
    )
    delivery_min_days = models.PositiveSmallIntegerField(null=True, blank=True)
    delivery_max_days = models.PositiveSmallIntegerField(null=True, blank=True)
    delivery_estimate_status = models.CharField(
        max_length=10, choices=EstimateStatus.choices, default=EstimateStatus.UNKNOWN
    )
    requires_tracking = models.BooleanField(
        default=False, help_text="An order cannot be marked shipped without a tracking number"
    )
    tracking_url_template = models.CharField(
        max_length=300, blank=True, help_text="https URL containing {tracking_number}"
    )
    max_weight_grams = models.PositiveIntegerField(null=True, blank=True)
    config = models.JSONField(default=dict, blank=True, help_text="Adapter-specific, non-secret settings")

    class Meta:
        db_table = "shipping_method"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.code

    def name(self, lang: str = "fa") -> str:
        return self.names.get(lang) or self.names.get("fa") or self.names.get("en") or self.code


# ------------------------------------------------------------------------------------------------ Phase 8
class ShippingZone(models.Model):
    """A set of destinations. A zone with `regions` is narrower than the same country without them (priority breaks ties)."""

    code = models.SlugField(max_length=40, unique=True)
    names = models.JSONField(default=dict, blank=True)
    countries = models.JSONField(default=list, help_text="ISO 3166-1 alpha-2 codes")
    regions = models.JSONField(
        default=list,
        blank=True,
        help_text="Provinces / states (any spelling, Persian or Latin); empty = the whole country",
    )
    priority = models.PositiveIntegerField(default=100, help_text="Lower wins when several zones match")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "shipping_zone"
        ordering = ["priority", "id"]

    def __str__(self):
        return self.code


class ShippingRate(models.Model):
    """The price of one method inside one zone. fee = base + per_kg for weight above `included_weight_grams`."""

    method = models.ForeignKey(ShippingMethod, on_delete=models.CASCADE, related_name="rates")
    zone = models.ForeignKey(ShippingZone, on_delete=models.PROTECT, related_name="rates")
    base_fee_irr = models.DecimalField(max_digits=18, decimal_places=0, validators=[MinValueValidator(Decimal("0"))])
    per_kg_irr = models.DecimalField(
        max_digits=18, decimal_places=0, default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))]
    )
    included_weight_grams = models.PositiveIntegerField(default=0)
    min_subtotal_irr = models.DecimalField(max_digits=18, decimal_places=0, default=Decimal("0"))
    max_subtotal_irr = models.DecimalField(max_digits=18, decimal_places=0, null=True, blank=True)
    free_over_irr = models.DecimalField(max_digits=18, decimal_places=0, null=True, blank=True)
    value_status = models.CharField(max_length=10, choices=FeeStatus.choices, default=FeeStatus.ESTIMATED)
    delivery_min_days = models.PositiveSmallIntegerField(null=True, blank=True)
    delivery_max_days = models.PositiveSmallIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "shipping_rate"
        ordering = ["sort_order", "id"]
        constraints = [models.CheckConstraint(condition=models.Q(base_fee_irr__gte=0), name="shiprate_fee_nonneg")]


class CustomsMode(models.TextChoices):
    UNKNOWN = "unknown", "Not known (shown as unknown, never as zero)"
    NOT_APPLICABLE = "not_applicable", "No customs (domestic / customs union)"
    ON_DELIVERY = "on_delivery", "Duties and taxes collected from the customer on delivery (not in our total)"
    PREPAID = "prepaid", "Duties and taxes collected by us at checkout"


class CountryShippingPolicy(models.Model):
    """Per-destination rules: address requirements, customs treatment, customer-facing policy text, restrictions."""

    country = models.CharField(max_length=2, unique=True)
    names = models.JSONField(default=dict, blank=True)
    is_shippable = models.BooleanField(
        default=True, help_text="False blocks the country even if a zone lists it (e.g. legal restriction)"
    )
    address_requirements = models.JSONField(
        default=dict, blank=True,
        help_text='Overrides: {"required": ["province","postal_code"], "postal_code_pattern": "^\\d{5}$", "phone_required": true}',
    )  # fmt: skip
    customs_mode = models.CharField(max_length=14, choices=CustomsMode.choices, default=CustomsMode.UNKNOWN)
    customs_notice = models.JSONField(default=dict, blank=True, help_text="Localized text shown to the customer")
    taxes_notice = models.JSONField(default=dict, blank=True)
    policy_notes = models.JSONField(
        default=dict, blank=True, help_text="Localized shipping / returns notes for this country"
    )
    restricted_category_slugs = models.JSONField(default=list, blank=True)
    default_display_currency = models.CharField(max_length=3, blank=True)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "shipping_country_policy"
        ordering = ["country"]

    def __str__(self):
        return self.country

    def text(self, field: str, lang: str) -> str:
        data = getattr(self, field) or {}
        return data.get(lang) or data.get("en") or data.get("fa") or ""


class ShipmentStatus(models.TextChoices):
    PENDING = "pending", "Preparing"
    IN_TRANSIT = "in_transit", "In transit"
    OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
    DELIVERED = "delivered", "Delivered"
    FAILED_ATTEMPT = "failed_attempt", "Delivery attempt failed"
    RETURNED = "returned", "Returned to sender"
    CANCELLED = "cancelled", "Cancelled"
    EXCEPTION = "exception", "Problem with the shipment"


class Shipment(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="shipments")
    method_code = models.CharField(max_length=40, blank=True)
    provider_key = models.SlugField(max_length=30, default="table")
    carrier = models.CharField(max_length=60, blank=True)
    tracking_number = models.CharField(max_length=80, blank=True)
    tracking_url = models.URLField(max_length=300, blank=True)
    carrier_reference = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=ShipmentStatus.choices, default=ShipmentStatus.PENDING)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    carrier_estimated_delivery_at = models.DateTimeField(
        null=True, blank=True, help_text="Only ever set from a carrier response, never invented"
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "shipping_shipment"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["status", "provider_key"], name="shipment_status_prov_idx")]


class EventSource(models.TextChoices):
    STAFF = "staff", "Staff"
    CARRIER = "carrier", "Carrier"
    SYSTEM = "system", "System"


class ShipmentEvent(models.Model):
    """Append-only shipment history (what happened, where, who/what reported it)."""

    shipment = models.ForeignKey(Shipment, on_delete=models.PROTECT, related_name="events")
    status = models.CharField(max_length=20, choices=ShipmentStatus.choices)
    description = models.CharField(max_length=300, blank=True)
    location = models.CharField(max_length=120, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    source = models.CharField(max_length=10, choices=EventSource.choices, default=EventSource.STAFF)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    external_id = models.CharField(max_length=120, blank=True)
    raw = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "shipping_shipment_event"
        ordering = ["occurred_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["shipment", "external_id"], condition=~models.Q(external_id=""), name="shipevent_external_uniq"
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("Shipment history is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Shipment history cannot be deleted.")
