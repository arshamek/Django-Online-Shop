"""
Payments. A payment belongs to a CHECKOUT (the customer pays once for every order the checkout produced).
The amount is always computed on the server from the orders; a payment only becomes CAPTURED through a verified
provider event (webhook / server-side verification) - never because the browser says so.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class PaymentStatus(models.TextChoices):
    INITIATED = "initiated", "Initiated"
    PENDING = "pending", "Waiting for the customer / provider"
    CAPTURED = "captured", "Captured"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"


LIVE = (PaymentStatus.INITIATED, PaymentStatus.PENDING)


class Payment(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    checkout = models.ForeignKey("orders.Checkout", on_delete=models.PROTECT, related_name="payments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments")
    provider = models.CharField(max_length=30)
    amount_irr = models.DecimalField(max_digits=18, decimal_places=0, validators=[MinValueValidator(Decimal("1"))])
    currency = models.CharField(max_length=3, default="IRR")
    status = models.CharField(max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.INITIATED)
    idempotency_key = models.CharField(max_length=80)
    provider_ref = models.CharField(max_length=120, blank=True)
    redirect_url = models.URLField(max_length=500, blank=True)
    expires_at = models.DateTimeField()
    captured_at = models.DateTimeField(null=True, blank=True)
    captured_amount_irr = models.DecimalField(max_digits=18, decimal_places=0, null=True, blank=True)

    # What the provider is actually asked to charge. IRR providers: equal to amount_irr. Foreign-currency providers:
    # amount_irr converted at `fx_rate` (frozen here; the callback must match it exactly).
    charge_currency = models.CharField(max_length=3, default="IRR")
    charge_amount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("0"))
    fx_rate = models.DecimalField(
        max_digits=24, decimal_places=10, null=True, blank=True, help_text="IRR per 1 charge currency"
    )
    fx_rate_source = models.CharField(max_length=40, blank=True)
    provider_mode = models.CharField(
        max_length=10, blank=True, help_text="sandbox | live | mock, as configured at initiation"
    )

    refunded_irr = models.DecimalField(max_digits=18, decimal_places=0, default=Decimal("0"))
    refunded_charge_amount = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("0"))
    failure_code = models.CharField(max_length=40, blank=True)
    failure_reason = models.CharField(max_length=300, blank=True)
    verified_at = models.DateTimeField(
        null=True, blank=True, help_text="Last successful server-to-server check with the provider"
    )
    needs_review = models.BooleanField(default=False)
    review_reason = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payments_payment"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(refunded_irr__lte=models.F("amount_irr")), name="payment_refund_le_amount"
            ),
            models.UniqueConstraint(fields=["user", "idempotency_key"], name="payment_user_idem_uniq"),
            models.UniqueConstraint(
                fields=["provider", "provider_ref"],
                condition=~models.Q(provider_ref=""),
                name="payment_provider_ref_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "expires_at"], name="payment_status_exp_idx"),
            models.Index(fields=["needs_review", "status"], name="payment_review_idx"),
        ]

    @property
    def merchant_ref(self) -> str:
        """Our own unguessable reference sent to the provider; every callback must echo it (reference validation)."""
        return self.public_id.hex


class TransactionKind(models.TextChoices):
    INITIATE = "initiate", "Initiate"
    CAPTURE = "capture", "Capture"
    FAILURE = "failure", "Failure"
    REFUND = "refund", "Refund"
    MISMATCH = "mismatch", "Amount / currency mismatch (not applied)"
    VERIFY = "verify", "Server-side verification"
    EXPIRE = "expire", "Expired"
    CANCEL = "cancel", "Cancelled"
    REFUND_FAILED = "refund_failed", "Refund refused by provider"


class PaymentTransaction(models.Model):
    """Append-only ledger of provider events; (payment, kind, event_id) is unique so a replayed webhook is a no-op."""

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=20, choices=TransactionKind.choices)
    amount_irr = models.DecimalField(max_digits=18, decimal_places=0, default=Decimal("0"))
    amount = models.DecimalField(
        max_digits=18, decimal_places=4, null=True, blank=True, help_text="In the charge currency"
    )
    currency = models.CharField(max_length=3, blank=True)
    event_id = models.CharField(max_length=120, blank=True)
    provider_ref = models.CharField(max_length=120, blank=True)
    raw = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "payments_transaction"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["payment", "kind", "event_id"], condition=~models.Q(event_id=""), name="paytx_event_uniq"
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("Payment transactions are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Payment transactions cannot be deleted.")


# ------------------------------------------------------------------------------------------------ Phase 8
class ProviderKind(models.TextChoices):
    IRANIAN = "iranian", "Iranian gateway"
    INTERNATIONAL = "international", "International provider"


class EligibilityStatus(models.TextChoices):
    UNVERIFIED = "unverified", "Not verified (cannot be enabled)"
    VERIFIED = "verified", "Merchant eligibility verified"
    NOT_ELIGIBLE = "not_eligible", "Not eligible"


class ProviderMode(models.TextChoices):
    SANDBOX = "sandbox", "Sandbox"
    LIVE = "live", "Live"


class PaymentProviderConfig(models.Model):
    """
    Administrator-owned settings of one payment adapter. NEVER holds credentials (those live in the environment).

    A real provider is only usable when `is_enabled` AND `eligibility_status == verified`: nobody is allowed to assume
    that a gateway accepts a merchant registered in a given country (see docs/payments/PROVIDER_INTEGRATION_GUIDE.md).
    Live mode additionally needs a recorded sandbox verification.
    """

    key = models.SlugField(
        max_length=30, unique=True, help_text="Adapter key, e.g. the key of a registered provider class"
    )
    names = models.JSONField(default=dict, blank=True, help_text='Customer-facing names {"fa": "...", "en": "..."}')
    is_enabled = models.BooleanField(default=False)
    mode = models.CharField(max_length=10, choices=ProviderMode.choices, default=ProviderMode.SANDBOX)
    eligibility_status = models.CharField(
        max_length=14, choices=EligibilityStatus.choices, default=EligibilityStatus.UNVERIFIED
    )
    eligibility_notes = models.TextField(
        blank=True, help_text="Who verified it, with which document / contract / support ticket, and when"
    )
    merchant_country = models.CharField(
        max_length=2, blank=True, help_text="Country the merchant account is registered in"
    )
    supported_currencies = models.JSONField(
        default=list, blank=True, help_text="Narrows the adapter's currencies; empty = adapter default"
    )
    default_charge_currency = models.CharField(max_length=3, blank=True)
    customer_countries = models.JSONField(
        default=list, blank=True, help_text="ISO codes of customers allowed to use it; empty = all"
    )
    settlement_currency = models.CharField(max_length=3, blank=True)
    settlement_notes = models.TextField(blank=True)
    legal_notes = models.TextField(blank=True, help_text="Licences, restrictions, sanctions / compliance review")
    min_amount_irr = models.DecimalField(max_digits=18, decimal_places=0, null=True, blank=True)
    max_amount_irr = models.DecimalField(max_digits=18, decimal_places=0, null=True, blank=True)
    sandbox_verified_at = models.DateTimeField(
        null=True, blank=True, help_text="An authorised sandbox account completed a full test flow"
    )
    sort_order = models.PositiveIntegerField(default=0)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payments_provider_config"
        ordering = ["sort_order", "key"]

    def __str__(self):
        return self.key

    def name(self, lang: str = "fa") -> str:
        return self.names.get(lang) or self.names.get("fa") or self.names.get("en") or self.key


class PaymentRefundStatus(models.TextChoices):
    PENDING = "pending", "Sent to the provider, outcome unknown"
    SUCCEEDED = "succeeded", "Refunded by the provider"
    FAILED = "failed", "Refused by the provider"
    MANUAL = "manual", "Refunded outside the system (recorded by staff)"


COUNTING_PAYMENT_REFUND = (PaymentRefundStatus.PENDING, PaymentRefundStatus.SUCCEEDED, PaymentRefundStatus.MANUAL)


class PaymentRefund(models.Model):
    """Money sent back through (or recorded against) one captured payment. One row per orders.Refund (idempotent)."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    order_refund = models.ForeignKey(
        "orders.Refund", null=True, blank=True, on_delete=models.PROTECT, related_name="payment_refunds"
    )
    idempotency_key = models.CharField(max_length=80)
    status = models.CharField(max_length=10, choices=PaymentRefundStatus.choices, default=PaymentRefundStatus.PENDING)
    amount_irr = models.DecimalField(max_digits=18, decimal_places=0, validators=[MinValueValidator(Decimal("1"))])
    charge_amount = models.DecimalField(max_digits=18, decimal_places=4)
    charge_currency = models.CharField(max_length=3)
    provider_ref = models.CharField(max_length=120, blank=True)
    error_code = models.CharField(max_length=40, blank=True)
    error_message = models.CharField(max_length=300, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "payments_refund"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["payment", "idempotency_key"], name="payrefund_idem_uniq"),
            models.UniqueConstraint(
                fields=["order_refund"],
                condition=models.Q(order_refund__isnull=False),
                name="payrefund_order_refund_uniq",
            ),
            models.CheckConstraint(condition=models.Q(amount_irr__gt=0), name="payrefund_amount_positive"),
        ]


class CallbackOutcome(models.TextChoices):
    APPLIED = "applied", "Applied"
    DUPLICATE = "duplicate", "Duplicate"
    IGNORED = "ignored", "Ignored"
    REJECTED = "rejected", "Rejected (not authentic)"


class ProviderCallback(models.Model):
    """Every provider callback / browser return that reached us, including rejected ones (forensics, never trusted)."""

    provider = models.CharField(max_length=30)
    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.PROTECT, related_name="callbacks")
    outcome = models.CharField(max_length=10, choices=CallbackOutcome.choices)
    reason = models.CharField(max_length=200, blank=True)
    event_id = models.CharField(max_length=120, blank=True)
    body_sha256 = models.CharField(max_length=64)
    remote_ip = models.GenericIPAddressField(null=True, blank=True)
    received_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "payments_callback"
        ordering = ["-received_at", "-id"]
        indexes = [models.Index(fields=["provider", "outcome", "-received_at"], name="paycb_provider_outcome_idx")]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("Callback receipts are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Callback receipts cannot be deleted.")


class ReconciliationRun(models.Model):
    provider = models.CharField(max_length=30, blank=True, help_text="Empty = every provider")
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    scanned = models.PositiveIntegerField(default=0)
    applied = models.PositiveIntegerField(default=0)
    errors = models.PositiveIntegerField(default=0)
    discrepancies = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "payments_reconciliation_run"
        ordering = ["-started_at", "-id"]
