"""
Translation workflow (docs/pricing/PHASE6_OVERVIEW.md).

Translated text lives in the *owner's* tables (ProductTranslation, CategoryTranslation, ...). This app adds:
the job queue, a content-addressed cache, per-row status for owners that do not carry one, and protected terms.
Customer reviews are deliberately not translatable (see services/entities.py).
"""

from django.db import models
from django.utils import timezone

from apps.catalog.models import TranslationOrigin, TranslationStatus
from apps.common.languages import LANGUAGE_CHOICES


class EntityType(models.TextChoices):
    PRODUCT = "product", "Product (title, descriptions, specifications)"
    CATEGORY = "category", "Category"
    ATTRIBUTE = "attribute", "Attribute name (e.g. Colour)"
    OPTION = "option", "Attribute value (e.g. Black)"


class JobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed (will retry)"
    DEAD = "dead", "Failed permanently (needs staff)"
    SKIPPED = "skipped", "Skipped"


class TranslationJob(models.Model):
    entity_type = models.CharField(max_length=10, choices=EntityType.choices)
    entity_id = models.BigIntegerField()
    source_language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES)
    target_language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES)
    source_text_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=10, choices=JobStatus.choices, default=JobStatus.QUEUED)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    locked_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=60, blank=True)
    error_message = models.CharField(max_length=300, blank=True)
    provider = models.CharField(max_length=60, blank=True)
    model = models.CharField(max_length=60, blank=True)
    units_total = models.PositiveIntegerField(default=0)
    units_from_cache = models.PositiveIntegerField(default=0)
    chars_sent = models.PositiveIntegerField(default=0)
    result = models.JSONField(default=dict, blank=True)
    requested_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "translations_job"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["entity_type", "entity_id", "target_language", "source_text_hash"],
                name="tjob_entity_lang_hash_uniq",
            ),
            models.CheckConstraint(
                condition=~models.Q(source_language=models.F("target_language")), name="tjob_languages_differ"
            ),
        ]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="tjob_status_next_idx"),
            models.Index(fields=["entity_type", "entity_id"], name="tjob_entity_idx"),
        ]


class TranslationCache(models.Model):
    """Same text + languages + provider + model + prompt version + protected-term set = same answer, so never pay twice."""

    key = models.CharField(max_length=64, unique=True)
    source_language = models.CharField(max_length=5)
    target_language = models.CharField(max_length=5)
    provider = models.CharField(max_length=60)
    model = models.CharField(max_length=60)
    translated_masked = models.TextField(help_text="Translation with protected-token placeholders still in place.")
    hit_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "translations_cache"
        indexes = [models.Index(fields=["last_used_at"], name="tcache_used_idx")]


class TranslationRecord(models.Model):
    """Status / provenance for translated rows whose owner table has no such columns (category, attribute, option, spec)."""

    entity_type = models.CharField(max_length=12)  # category | attribute | option | spec_value
    entity_id = models.BigIntegerField()
    language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES)
    target_object_id = models.BigIntegerField(null=True, blank=True, help_text="spec_value: the translated row.")
    origin = models.CharField(max_length=10, choices=TranslationOrigin.choices, default=TranslationOrigin.MACHINE)
    status = models.CharField(max_length=14, choices=TranslationStatus.choices, default=TranslationStatus.MACHINE)
    source_text_hash = models.CharField(max_length=64, blank=True)
    provider = models.CharField(max_length=60, blank=True)
    model = models.CharField(max_length=60, blank=True)
    reviewed_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "translations_record"
        constraints = [
            models.UniqueConstraint(fields=["entity_type", "entity_id", "language"], name="trecord_entity_lang_uniq")
        ]


class ProtectedTerm(models.Model):
    """Extra words that must never be translated (series names, trademarks). Brand names and codes are automatic."""

    term = models.CharField(max_length=120, unique=True)
    note = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "translations_protected_term"
        ordering = ["term"]

    def __str__(self):
        return self.term
