"""
Currencies and exchange rates (docs/pricing/PHASE6_OVERVIEW.md).

* IRR is the accounting currency. IRT (Toman) is a display unit: 1 IRT = `base_factor` (10) IRR.
* `ExchangeRate` rows are append-only history. Which row is "the" rate is decided by a `RateRoute`
  (ordered sources, maximum age) that an administrator configures: a nominal market rate is never
  assumed to be the rate at which goods can actually be bought.
* Every rate is a `Decimal`. Never a float.
"""

import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

ACCOUNTING_CURRENCY = "IRR"
DISPLAY_UNIT_CURRENCY = "IRT"


class Currency(models.Model):
    code = models.CharField(max_length=3, primary_key=True, help_text="ISO 4217, plus IRT (Toman).")
    name = models.CharField(max_length=60)
    symbol = models.CharField(max_length=8, blank=True)
    decimal_places = models.PositiveSmallIntegerField(default=2)
    base_currency = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="units", help_text="IRT -> IRR."
    )
    base_factor = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        default=Decimal("1"),
        validators=[MinValueValidator(Decimal("0.000001"))],
        help_text="1 unit of this currency = base_factor units of base_currency (IRT: 10 IRR).",
    )
    is_source = models.BooleanField(default=False, help_text="Source stores quote prices in this currency.")
    is_display = models.BooleanField(default=False, help_text="Customers may choose to see prices in it.")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "currencies_currency"
        ordering = ["sort_order", "code"]
        verbose_name_plural = "currencies"

    def __str__(self):
        return self.code

    @property
    def is_display_unit(self) -> bool:
        return self.base_currency_id is not None

    def clean(self):
        if self.base_currency_id and self.base_currency_id == self.code:
            raise ValidationError({"base_currency": "A currency cannot be its own base."})


class RateKind(models.TextChoices):
    MARKET = "market", "Nominal market rate (reference only)"
    OFFICIAL = "official", "Official / bank rate"
    BUREAU = "bureau", "Exchange-bureau rate"
    PURCHASE = "purchase", "Rate actually obtainable for purchases"
    MANUAL = "manual", "Entered manually"
    OTHER = "other", "Other"


class ProviderKey(models.TextChoices):
    MANUAL = "manual", "Manual entry (not polled)"
    FIXTURE = "fixture", "Fixture (synthetic numbers, never a market source)"
    HTTP_JSON = "http_json", "HTTP JSON API (configurable)"


class Health(models.TextChoices):
    UNKNOWN = "unknown", "Not polled yet"
    HEALTHY = "healthy", "Healthy"
    DEGRADED = "degraded", "Degraded"
    FAILING = "failing", "Failing"
    DISABLED = "disabled", "Disabled"


class ExchangeRateSource(models.Model):
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    provider_key = models.CharField(max_length=20, choices=ProviderKey.choices, default=ProviderKey.MANUAL)
    kind = models.CharField(max_length=10, choices=RateKind.choices, default=RateKind.OTHER)
    description = models.TextField(blank=True, help_text="What this rate really represents and its terms of use.")
    config = models.JSONField(default=dict, blank=True, help_text="Non-secret settings for the adapter.")
    credential_ref = models.CharField(max_length=40, blank=True, help_text="Env var CURRENCY_CRED_<REF> holds the key.")
    is_enabled = models.BooleanField(default=True)
    poll_interval_seconds = models.PositiveIntegerField(default=900)
    max_observation_age_seconds = models.PositiveIntegerField(
        default=6 * 3600, help_text="Observations whose own timestamp is older than this are rejected."
    )
    max_jump_percent = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="A change larger than this vs the previous value is quarantined for review. Empty = off.",
    )
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    consecutive_failures = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "currencies_source"
        ordering = ["code"]

    def __str__(self):
        return self.code

    @property
    def is_polled(self) -> bool:
        return self.provider_key != ProviderKey.MANUAL


class RateStatus(models.TextChoices):
    ACCEPTED = "accepted", "Accepted (usable)"
    QUARANTINED = "quarantined", "Quarantined (needs review, not used)"
    REJECTED = "rejected", "Rejected"


class ExchangeRate(models.Model):
    """One observation: `rate` units of `quote` per 1 unit of `base`. Append-only (only `status` may change)."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    source = models.ForeignKey(ExchangeRateSource, on_delete=models.PROTECT, related_name="rates")
    base = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    quote = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    rate = models.DecimalField(
        max_digits=24, decimal_places=10, validators=[MinValueValidator(Decimal("0.0000000001"))]
    )
    rate_at = models.DateTimeField(help_text="When the rate was valid (provider timestamp / moment of manual entry).")
    fetched_at = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField(null=True, blank=True, help_text="Explicit expiry (manual rates).")
    status = models.CharField(max_length=12, choices=RateStatus.choices, default=RateStatus.ACCEPTED)
    status_reason = models.CharField(max_length=200, blank=True)
    is_manual = models.BooleanField(default=False)
    created_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        db_table = "currencies_rate"
        constraints = [models.CheckConstraint(condition=models.Q(rate__gt=0), name="xrate_rate_positive")]
        indexes = [
            models.Index(fields=["base", "quote", "source", "-rate_at"], name="xrate_pair_src_at_idx"),
            models.Index(fields=["-rate_at"], name="xrate_at_idx"),
        ]

    def __str__(self):
        return f"{self.base_id}/{self.quote_id}={self.rate}"

    _MUTABLE = {"status", "status_reason"}

    def save(self, *args, **kwargs):
        if self.pk is not None:
            fields = kwargs.get("update_fields")
            if fields is None or not set(fields) <= self._MUTABLE:
                raise RuntimeError(
                    "ExchangeRate rows are append-only (only status may be updated, with update_fields)."
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("ExchangeRate rows are history and cannot be deleted.")


class RatePurpose(models.TextChoices):
    PURCHASE = "purchase", "Buying goods (cost side)"
    DISPLAY = "display", "Showing prices to customers in another currency"


class RateRoute(models.Model):
    """Which sources provide the rate for (base -> quote, purpose), in which order, and when it counts as stale."""

    base = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    quote = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    purpose = models.CharField(max_length=10, choices=RatePurpose.choices, default=RatePurpose.PURCHASE)
    max_age_seconds = models.PositiveIntegerField(help_text="Older than this = stale. Required: no default is assumed.")
    allow_stale_fallback = models.BooleanField(
        default=False, help_text="Use the freshest stale rate (flagged) when nothing fresh exists. Off = refuse."
    )
    is_enabled = models.BooleanField(default=True)
    note = models.CharField(max_length=300, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "currencies_route"
        constraints = [models.UniqueConstraint(fields=["base", "quote", "purpose"], name="route_pair_purpose_uniq")]

    def __str__(self):
        return f"{self.base_id}->{self.quote_id} ({self.purpose})"


class RateRouteSource(models.Model):
    route = models.ForeignKey(RateRoute, on_delete=models.CASCADE, related_name="route_sources")
    source = models.ForeignKey(ExchangeRateSource, on_delete=models.CASCADE, related_name="+")
    priority = models.PositiveIntegerField(default=10, help_text="Lowest number is tried first.")

    class Meta:
        db_table = "currencies_route_source"
        ordering = ["priority", "id"]
        constraints = [models.UniqueConstraint(fields=["route", "source"], name="routesource_uniq")]


class RateFetchLog(models.Model):
    source = models.ForeignKey(ExchangeRateSource, on_delete=models.CASCADE, related_name="fetch_logs")
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    ok = models.BooleanField(default=False)
    n_received = models.PositiveIntegerField(default=0)
    n_accepted = models.PositiveIntegerField(default=0)
    n_quarantined = models.PositiveIntegerField(default=0)
    n_rejected = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=60, blank=True)
    error_message = models.CharField(max_length=300, blank=True)
    details = models.JSONField(default=list, blank=True)
    latency_ms = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "currencies_fetch_log"
        ordering = ["-started_at", "-id"]
        indexes = [models.Index(fields=["source", "-started_at"], name="fetchlog_src_start_idx")]
