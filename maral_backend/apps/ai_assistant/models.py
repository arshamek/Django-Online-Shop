"""
Phase 9 data model. Deliberately small:

* `AssistantConfig` - one staff-editable row (no secrets: the API key lives in the environment).
* `Conversation` / `Message` - the *current* conversation only; deleted by the customer or by the retention job.
* `UsageRecord` - per-turn usage and error statistics (no message text).
* `PurchaseInquiry` - a request for staff to look at an external product. It is NOT an order and has no payment.
"""

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from .constants import ALL_SOURCES, SUPPORTED_LANGUAGES


def default_languages():
    return list(SUPPORTED_LANGUAGES)


def default_sources():
    return list(ALL_SOURCES)


class AssistantProvider(models.TextChoices):
    MOCK = "mock", "Deterministic mock (demo mode)"
    ANTHROPIC = "anthropic", "Anthropic"


class AssistantConfig(models.Model):
    """Singleton (pk=1). Read it with `AssistantConfig.load()`."""

    enabled = models.BooleanField(default=True)
    provider = models.CharField(max_length=20, choices=AssistantProvider.choices, default=AssistantProvider.MOCK)
    model = models.CharField(max_length=80, blank=True, help_text="Empty = settings.AI_SETTINGS['model'].")
    supported_languages = models.JSONField(default=default_languages)
    extra_instructions = models.TextField(
        blank=True,
        max_length=2000,
        help_text="Staff-written tone/style notes appended to the built-in system prompt. They can never relax its safety rules.",
    )
    enabled_sources = models.JSONField(default=default_sources)
    daily_message_limit = models.PositiveIntegerField(default=100, validators=[MaxValueValidator(10_000)])
    max_input_chars = models.PositiveIntegerField(
        default=1000, validators=[MinValueValidator(50), MaxValueValidator(4000)]
    )
    context_messages = models.PositiveSmallIntegerField(
        default=8,
        validators=[MaxValueValidator(30)],
        help_text="Recent messages of THIS conversation given to the model.",
    )
    retention_days = models.PositiveSmallIntegerField(
        default=30,
        validators=[MinValueValidator(1), MaxValueValidator(365)],
        help_text="Conversations idle for longer than this are deleted by `purge_ai_conversations`.",
    )
    max_results = models.PositiveSmallIntegerField(default=6, validators=[MinValueValidator(1), MaxValueValidator(12)])
    price_max_age_hours = models.PositiveSmallIntegerField(
        default=72, validators=[MinValueValidator(1)], help_text="Older source prices are shown as provisional (stale)."
    )
    provider_timeout_seconds = models.PositiveSmallIntegerField(
        default=20, validators=[MinValueValidator(2), MaxValueValidator(60)]
    )
    allow_fixture_snapshots = models.BooleanField(
        default=False, help_text="Allow synthetic fixture rows to appear as snapshots (demo/staging only)."
    )
    expose_marketplace_urls = models.BooleanField(
        default=False,
        help_text="Show the original marketplace URL to customers (the import module treats it as staff-only).",
    )
    last_provider_test_at = models.DateTimeField(null=True, blank=True)
    last_provider_test_ok = models.BooleanField(null=True, blank=True)
    last_provider_test_error = models.CharField(max_length=200, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_assistant_config"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "AssistantConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Conversation(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_conversations")
    title = models.CharField(max_length=80, blank=True)
    language = models.CharField(max_length=5, default="fa")
    state = models.JSONField(
        default=dict, blank=True, help_text="Context of THIS conversation only (last filters/results)."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_assistant_conversation"
        ordering = ["-updated_at", "-id"]
        indexes = [models.Index(fields=["user", "-updated_at"], name="aiconv_user_upd_idx")]


class MessageRole(models.TextChoices):
    USER = "user", "Customer"
    ASSISTANT = "assistant", "Assistant"


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=MessageRole.choices)
    content = models.TextField(max_length=4000)
    language = models.CharField(max_length=5, blank=True)
    data = models.JSONField(
        default=dict, blank=True, help_text="Assistant turns: product refs, provider status, clarification."
    )
    demo_mode = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ai_assistant_message"
        ordering = ["id"]
        indexes = [models.Index(fields=["conversation", "id"], name="aimsg_conv_id_idx")]


class UsageStatus(models.TextChoices):
    OK = "ok", "OK"
    FALLBACK = "fallback", "Model reply rejected, template used"
    PROVIDER_ERROR = "provider_error", "Provider error"
    LIMITED = "limited", "Rejected by a usage limit"
    DISABLED = "disabled", "Assistant disabled"


class UsageRecord(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    conversation = models.ForeignKey(Conversation, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    provider = models.CharField(max_length=20)
    model = models.CharField(max_length=80, blank=True)
    demo_mode = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=UsageStatus.choices, default=UsageStatus.OK)
    error_code = models.CharField(max_length=60, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    flagged_content = models.BooleanField(default=False, help_text="Untrusted text looked like a prompt injection.")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ai_assistant_usage"
        indexes = [
            models.Index(fields=["user", "created_at"], name="aiusage_user_created_idx"),
            models.Index(fields=["created_at"], name="aiusage_created_idx"),
        ]


class InquiryStatus(models.TextChoices):
    NEW = "new", "New"
    UNDER_REVIEW = "under_review", "Under staff review"
    FEASIBLE = "feasible", "Staff confirmed it can be purchased"
    NOT_FEASIBLE = "not_feasible", "Cannot be purchased"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Cancelled by the customer"


class PurchaseInquiry(models.Model):
    """
    A customer's request that staff look into buying an external product for them. It is NOT an order: nothing
    here creates an order, a quote, a payment or a reservation. Staff must re-check price, availability and
    feasibility (the three `*_checked_at` stamps) before an inquiry can become FEASIBLE; turning it into an order
    still goes through the normal import -> publish -> cart -> checkout workflow.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="purchase_inquiries")
    conversation = models.ForeignKey(Conversation, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    source = models.ForeignKey("product_sources.ProductSource", on_delete=models.PROTECT, related_name="+")
    external_product = models.ForeignKey("product_sources.ExternalProduct", on_delete=models.PROTECT, related_name="+")
    external_id = models.CharField(max_length=120)
    source_url = models.URLField(max_length=500, blank=True, help_text="Original marketplace URL (staff).")
    title = models.CharField(max_length=500)
    brand = models.CharField(max_length=200, blank=True)
    size = models.CharField(max_length=80, blank=True)
    color = models.CharField(max_length=80, blank=True)
    quantity = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(10)])
    customer_note = models.CharField(max_length=500, blank=True)
    # What the customer saw (a snapshot; NOT a confirmed price).
    original_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    original_currency = models.CharField(max_length=3, blank=True)
    estimated_price_irr = models.DecimalField(max_digits=18, decimal_places=0, null=True, blank=True)
    estimated_price_status = models.CharField(max_length=14, blank=True)
    price_rule_version = models.PositiveIntegerField(null=True, blank=True)
    snapshot_observed_at = models.DateTimeField(null=True, blank=True)
    availability_seen = models.CharField(max_length=14, blank=True)
    status = models.CharField(max_length=14, choices=InquiryStatus.choices, default=InquiryStatus.NEW)
    # Staff verification trail.
    price_checked_at = models.DateTimeField(null=True, blank=True)
    availability_checked_at = models.DateTimeField(null=True, blank=True)
    feasibility_checked_at = models.DateTimeField(null=True, blank=True)
    staff_notes = models.TextField(blank=True, max_length=2000)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_assistant_purchase_inquiry"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="aiinq_user_created_idx"),
            models.Index(fields=["status", "-created_at"], name="aiinq_status_created_idx"),
        ]
