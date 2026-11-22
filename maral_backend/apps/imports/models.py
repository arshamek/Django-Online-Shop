"""
Import jobs (docs/database/DATABASE_DESIGN.md 2.5). A job is a durable, resumable unit of work: it keeps
a cursor, per-item results and an append-only log, so it can be cancelled, retried or recovered without
repeating finished work or losing what already happened.
"""

import uuid

from django.conf import settings
from django.db import models

from apps.product_sources.models import ProductSource


class JobType(models.TextChoices):
    FULL = "full", "Full import"
    INCREMENTAL = "incremental", "Incremental import (changed since)"
    SINGLE = "single", "Single product"
    BULK = "bulk", "Bulk (list of products)"
    CATEGORY = "category", "Category import"
    SYNC = "sync", "Synchronise known listings (prices / stock)"
    REPAIR = "repair", "Repair failed items"


class JobTrigger(models.TextChoices):
    MANUAL = "manual", "Started by staff"
    SCHEDULED = "scheduled", "Scheduled"


class JobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    WAITING = "waiting", "Waiting to retry (rate limit / transient failure)"
    SUCCEEDED = "succeeded", "Succeeded"
    PARTIALLY_FAILED = "partially_failed", "Partially failed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


ACTIVE_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.WAITING)
TERMINAL_STATUSES = (JobStatus.SUCCEEDED, JobStatus.PARTIALLY_FAILED, JobStatus.FAILED, JobStatus.CANCELLED)
RETRYABLE_STATUSES = (JobStatus.FAILED, JobStatus.PARTIALLY_FAILED, JobStatus.CANCELLED)


class ImportJob(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    source = models.ForeignKey(ProductSource, on_delete=models.PROTECT, related_name="import_jobs")
    job_type = models.CharField(max_length=12, choices=JobType.choices)
    trigger = models.CharField(max_length=10, choices=JobTrigger.choices, default=JobTrigger.MANUAL)
    status = models.CharField(max_length=16, choices=JobStatus.choices, default=JobStatus.QUEUED)
    params = models.JSONField(default=dict, blank=True)
    provider_mode = models.CharField(max_length=10, default="fixture", help_text="fixture | live")
    idempotency_key = models.CharField(max_length=120, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    cursor = models.CharField(max_length=500, blank=True, help_text="Checkpoint: where the next slice resumes.")
    cancel_requested = models.BooleanField(default=False)
    celery_task_id = models.CharField(max_length=64, blank=True)
    attempt = models.PositiveSmallIntegerField(default=0, help_text="Transient failures since the last (re)start.")
    retry_count = models.PositiveSmallIntegerField(default=0, help_text="Manual retries.")
    resume_at = models.DateTimeField(null=True, blank=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)

    n_total = models.PositiveIntegerField(null=True, blank=True, help_text="Provider-reported total, when known.")
    n_seen = models.PositiveIntegerField(default=0)
    n_created = models.PositiveIntegerField(default=0)
    n_updated = models.PositiveIntegerField(default=0)
    n_unchanged = models.PositiveIntegerField(default=0)
    n_skipped = models.PositiveIntegerField(default=0)
    n_failed = models.PositiveIntegerField(default=0)

    error_code = models.CharField(max_length=60, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "imports_job"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key"], condition=~models.Q(idempotency_key=""), name="importjob_idem_uniq"
            )
        ]
        indexes = [
            models.Index(fields=["source", "status"], name="importjob_src_status_idx"),
            models.Index(fields=["status", "heartbeat_at"], name="importjob_status_hb_idx"),
        ]

    def __str__(self):
        return f"{self.job_type}:{self.source_id}:{self.public_id}"

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def n_processed(self) -> int:
        return self.n_created + self.n_updated + self.n_unchanged + self.n_skipped + self.n_failed

    @property
    def progress_percent(self) -> float | None:
        if self.status == JobStatus.SUCCEEDED and not self.n_total:
            return 100.0
        if not self.n_total:
            return None
        return round(min(100.0, self.n_processed * 100.0 / self.n_total), 1)


class ItemAction(models.TextChoices):
    CREATED = "created", "New listing"
    UPDATED = "updated", "Listing changed"
    UNCHANGED = "unchanged", "No change"
    SKIPPED = "skipped", "Skipped"
    DELISTED = "delisted", "No longer listed at the source"
    FAILED = "failed", "Failed"
    DUPLICATE = "duplicate", "Duplicate within the job"


class ItemStatus(models.TextChoices):
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"


class ImportJobItem(models.Model):
    job = models.ForeignKey(ImportJob, on_delete=models.CASCADE, related_name="items")
    external_id = models.CharField(max_length=120)
    action = models.CharField(max_length=10, choices=ItemAction.choices, blank=True)
    status = models.CharField(max_length=10, choices=ItemStatus.choices, default=ItemStatus.FAILED)
    error_code = models.CharField(max_length=60, blank=True)
    error_message = models.CharField(max_length=300, blank=True)
    payload_hash = models.CharField(max_length=64, blank=True)
    external_product = models.ForeignKey(
        "product_sources.ExternalProduct", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "imports_job_item"
        constraints = [models.UniqueConstraint(fields=["job", "external_id"], name="importitem_job_extid_uniq")]
        indexes = [models.Index(fields=["job", "status"], name="importitem_job_status_idx")]


class LogLevel(models.TextChoices):
    DEBUG = "debug", "Debug"
    INFO = "info", "Info"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"


class ImportLog(models.Model):
    """Append-only. Holds ids and codes, never credentials or raw payloads. Pruned by `imports.purge_logs`."""

    job = models.ForeignKey(ImportJob, on_delete=models.CASCADE, related_name="logs")
    item = models.ForeignKey(ImportJobItem, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    level = models.CharField(max_length=8, choices=LogLevel.choices, default=LogLevel.INFO)
    message = models.CharField(max_length=500)
    context = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "imports_log"
        ordering = ["id"]
        indexes = [models.Index(fields=["job", "id"], name="importlog_job_id_idx")]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError("Import log entries cannot be modified.")
        super().save(*args, **kwargs)
