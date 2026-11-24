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
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payments_payment"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "idempotency_key"], name="payment_user_idem_uniq"),
            models.UniqueConstraint(
                fields=["provider", "provider_ref"], condition=~models.Q(provider_ref=""), name="payment_provider_ref_uniq"
            ),
        ]
        indexes = [models.Index(fields=["status", "expires_at"], name="payment_status_exp_idx")]


class TransactionKind(models.TextChoices):
    INITIATE = "initiate", "Initiate"
    CAPTURE = "capture", "Capture"
    FAILURE = "failure", "Failure"
    REFUND = "refund", "Refund"
    MISMATCH = "mismatch", "Amount mismatch (not applied)"


class PaymentTransaction(models.Model):
    """Append-only ledger of provider events; (payment, kind, event_id) is unique so a replayed webhook is a no-op."""

    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=10, choices=TransactionKind.choices)
    amount_irr = models.DecimalField(max_digits=18, decimal_places=0, default=Decimal("0"))
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
