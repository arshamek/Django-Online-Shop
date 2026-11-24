"""
Domestic delivery methods (what the checkout shows as «ارسال استاندارد / ارسال سریع»).

Fees are DATA, never code: nothing is seeded in production. International freight and customs for proxy items are a
different thing and live in the pricing engine's rules. `value_status` says whether the fee is a confirmed tariff or
an estimate; an unconfigured method is never priced as zero.
"""

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models


class FeeStatus(models.TextChoices):
    CONFIRMED = "confirmed", "Confirmed tariff"
    ESTIMATED = "estimated", "Estimated"


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

    class Meta:
        db_table = "shipping_method"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.code

    def name(self, lang: str = "fa") -> str:
        return self.names.get(lang) or self.names.get("fa") or self.names.get("en") or self.code
