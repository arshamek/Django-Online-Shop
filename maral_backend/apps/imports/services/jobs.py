"""
Import job management: create, run in time-boxed slices, cancel, retry, recover, summarise.

Design rules
* A job is resumable. `cursor` is saved after every page and every item has a unique (job, external_id)
  row, so a retry, a redelivered task or a recovered job never repeats finished work.
* A worker never sleeps. Rate limits and transient failures set the job WAITING with `resume_at` and the
  task is re-scheduled no sooner than the source allows; non-retryable errors (401/403, bad config) fail fast.
* One bad product never sinks a job: it becomes a FAILED item and the run continues (partial import).
"""

import time
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.common.exceptions import BusinessRuleViolation, Conflict, DomainError
from apps.product_sources.models import ExternalProduct, ListingStatus, ProductSource
from apps.product_sources.providers.errors import ProviderError, ProviderNotAuthorized, ProviderNotConfigured
from apps.product_sources.providers.registry import get_provider

from ..models import (
    ACTIVE_STATUSES,
    RETRYABLE_STATUSES,
    TERMINAL_STATUSES,
    ImportJob,
    ImportJobItem,
    ImportLog,
    ItemAction,
    ItemStatus,
    JobStatus,
    JobTrigger,
    JobType,
    LogLevel,
)
from .logs import log
from .pipeline import ItemFailure, process_product

DISCOVERY_TYPES = (JobType.FULL, JobType.INCREMENTAL, JobType.CATEGORY)
EXCLUSIVE_TYPES = (JobType.FULL, JobType.INCREMENTAL, JobType.SYNC)
MAX_BULK_IDS = 1000
CHUNK = 25


@dataclass
class RunOutcome:
    state: str  # done | continue | retry
    retry_after: float | None = None


class _Cancelled(Exception):
    pass


# ------------------------------------------------------------------------------------ creation
def _enqueue(job: ImportJob, countdown: float = 0) -> None:
    from .. import tasks

    transaction.on_commit(lambda: tasks.run_job.apply_async(args=[job.pk], countdown=countdown, queue="imports"))


def create_job(
    *,
    source: ProductSource,
    job_type: str,
    requested_by=None,
    params: dict | None = None,
    idempotency_key: str = "",
    trigger: str = JobTrigger.MANUAL,
) -> tuple[ImportJob, bool]:
    """Returns (job, created). Replaying an idempotency key returns the original job."""
    if idempotency_key:
        existing = ImportJob.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing, False
    params = dict(params or {})
    provider = get_provider(source)
    try:
        provider.assert_ready()
    except ProviderNotAuthorized as exc:
        raise BusinessRuleViolation(str(exc), code="source_not_authorized") from exc
    except ProviderNotConfigured as exc:
        raise BusinessRuleViolation(str(exc), code="source_not_configured") from exc
    caps = provider.capabilities
    if job_type in (JobType.SINGLE, JobType.BULK):
        ids = params.get("external_ids") or ([params["external_id"]] if params.get("external_id") else [])
        ids = list(dict.fromkeys(str(i).strip() for i in ids if str(i).strip()))
        if not ids or (job_type == JobType.SINGLE and len(ids) != 1) or len(ids) > MAX_BULK_IDS:
            raise DomainError(
                "Provide the product identifier(s) to import.",
                code="validation_error",
                fields={"external_ids": [f"1 to {MAX_BULK_IDS} identifiers required (exactly 1 for single)."]},
            )
        params = {"external_ids": ids}
    elif job_type == JobType.CATEGORY:
        if not caps.category_filter:
            raise BusinessRuleViolation(
                f"{source.code} cannot filter by category with its authorised access.", code="unsupported_job_type"
            )
        if not params.get("category"):
            raise DomainError("A category is required.", code="validation_error", fields={"category": ["Required."]})
        params = {"category": str(params["category"])}
    elif job_type == JobType.INCREMENTAL:
        if not caps.incremental:
            raise BusinessRuleViolation(f"{source.code} has no 'changed since' filter.", code="unsupported_job_type")
        since = params.get("since")
        if not since:
            last = ImportJob.objects.filter(
                source=source, status=JobStatus.SUCCEEDED, job_type__in=DISCOVERY_TYPES
            ).first()
            since = last.started_at.isoformat() if last and last.started_at else None
        if not since or parse_datetime(str(since)) is None:
            raise DomainError(
                "An ISO-8601 'since' is required.", code="validation_error", fields={"since": ["Required."]}
            )
        params = {"since": str(since)}
    elif job_type == JobType.REPAIR:
        if not params.get("repair_of"):
            raise DomainError("repair_of is required.", code="validation_error", fields={"repair_of": ["Required."]})
        params = {"repair_of": int(params["repair_of"])}
    elif job_type in (JobType.FULL, JobType.SYNC):
        params = {}
    else:
        raise DomainError("Unknown job type.", code="validation_error", fields={"job_type": ["Unknown."]})
    if (
        job_type in EXCLUSIVE_TYPES
        and ImportJob.objects.filter(source=source, job_type=job_type, status__in=ACTIVE_STATUSES).exists()
    ):
        raise Conflict("A job of this type is already active for this source.", code="import_already_running")
    try:
        with transaction.atomic():
            job = ImportJob.objects.create(
                source=source,
                job_type=job_type,
                trigger=trigger,
                params=params,
                provider_mode=provider.mode,
                idempotency_key=idempotency_key,
                requested_by=requested_by,
                scheduled_for=timezone.now() if trigger == JobTrigger.SCHEDULED else None,
                summary={"phase": "discover"},
            )
    except Exception:
        if idempotency_key:  # lost a race on the unique key
            existing = ImportJob.objects.filter(idempotency_key=idempotency_key).first()
            if existing:
                return existing, False
        raise
    log(job, LogLevel.INFO, "Job created.", job_type=job_type, mode=provider.mode, trigger=trigger)
    _enqueue(job)
    return job, True


# ------------------------------------------------------------------------------------ control
@transaction.atomic
def request_cancel(job: ImportJob, actor=None) -> ImportJob:
    job = ImportJob.objects.select_for_update().get(pk=job.pk)
    if job.status in TERMINAL_STATUSES:
        raise Conflict("The job has already finished.", code="job_finished")
    job.cancel_requested = True
    job.save(update_fields=["cancel_requested", "updated_at"])
    log(job, LogLevel.INFO, "Cancellation requested.")
    if job.status in (JobStatus.QUEUED, JobStatus.WAITING):  # no worker is running it: stop now
        _finish(job, JobStatus.CANCELLED)
    return job


@transaction.atomic
def retry_job(job: ImportJob, actor=None) -> ImportJob:
    job = ImportJob.objects.select_for_update().get(pk=job.pk)
    if job.status not in RETRYABLE_STATUSES:
        raise Conflict("Only failed, partially failed or cancelled jobs can be retried.", code="job_not_retryable")
    if (
        job.job_type in EXCLUSIVE_TYPES
        and ImportJob.objects.filter(source=job.source, job_type=job.job_type, status__in=ACTIVE_STATUSES)
        .exclude(pk=job.pk)
        .exists()
    ):
        raise Conflict("A job of this type is already active for this source.", code="import_already_running")
    summary = dict(job.summary or {})
    if job.job_type in DISCOVERY_TYPES and job.items.filter(status=ItemStatus.FAILED).exists():
        summary["phase"] = "repair"  # re-fetch failed items first, then carry on from the saved cursor
    elif summary.get("phase") == "complete" and job.job_type in DISCOVERY_TYPES:
        summary["phase"] = "complete"
    job.summary = summary
    job.status, job.cancel_requested, job.attempt = JobStatus.QUEUED, False, 0
    job.retry_count += 1
    job.error_code = job.error_message = ""
    job.finished_at = job.resume_at = None
    job.save()
    log(job, LogLevel.INFO, "Job re-queued by retry.", retry_count=job.retry_count)
    _enqueue(job)
    return job


def recover_stale() -> list[int]:
    """Re-queue jobs whose worker vanished (no heartbeat) or whose scheduled retry was lost."""
    now = timezone.now()
    cutoff = now - timedelta(minutes=settings.IMPORTS_STALE_JOB_MINUTES)
    recovered = []
    stale = (
        ImportJob.objects.filter(status=JobStatus.RUNNING, heartbeat_at__lt=cutoff)
        | ImportJob.objects.filter(status=JobStatus.QUEUED, updated_at__lt=cutoff)
        | ImportJob.objects.filter(status=JobStatus.WAITING, resume_at__lt=now - timedelta(minutes=2))
    )
    for job in stale.distinct():
        with transaction.atomic():
            job = ImportJob.objects.select_for_update().get(pk=job.pk)
            if job.status not in ACTIVE_STATUSES:
                continue
            job.status, job.heartbeat_at = JobStatus.QUEUED, None
            job.save(update_fields=["status", "heartbeat_at", "updated_at"])
            log(job, LogLevel.WARNING, "Recovered a stalled job.", previous_cursor=job.cursor)
            _enqueue(job)
            recovered.append(job.pk)
    return recovered


# ------------------------------------------------------------------------------------ running
def _backoff(attempt: int) -> float:
    return float(min(900, 30 * 2 ** max(0, attempt - 1)))


def _claim(job_id: int) -> tuple[ImportJob | None, RunOutcome | None]:
    with transaction.atomic():
        job = ImportJob.objects.select_for_update().filter(pk=job_id).first()
        now = timezone.now()
        if job is None or job.status in TERMINAL_STATUSES:
            return None, RunOutcome("done")
        if job.cancel_requested:
            _finish(job, JobStatus.CANCELLED)
            return None, RunOutcome("done")
        stale = job.heartbeat_at is None or job.heartbeat_at < now - timedelta(
            minutes=settings.IMPORTS_STALE_JOB_MINUTES
        )
        if job.status == JobStatus.RUNNING and not stale:
            return None, RunOutcome("done")  # another worker owns it
        if job.status == JobStatus.WAITING and job.resume_at and job.resume_at > now:
            return None, RunOutcome("retry", (job.resume_at - now).total_seconds())
        job.status, job.heartbeat_at, job.resume_at = JobStatus.RUNNING, now, None
        job.started_at = job.started_at or now
        job.save(update_fields=["status", "heartbeat_at", "resume_at", "started_at", "updated_at"])
        return job, None


def run_slice(job_id: int) -> RunOutcome:
    job, early = _claim(job_id)
    if early is not None:
        return early
    deadline = time.monotonic() + settings.IMPORTS_SLICE_SECONDS
    try:
        provider = get_provider(job.source, mode=job.provider_mode)
        provider.assert_ready()
        finished = _run_work(job, provider, deadline)
    except _Cancelled:
        _recount(job)
        _finish(job, JobStatus.CANCELLED)
        return RunOutcome("done")
    except ProviderError as exc:
        _recount(job)
        if exc.retryable:
            return _wait(job, exc)
        _fail(job, exc.code, str(exc))
        return RunOutcome("done")
    except Exception as exc:  # a bug must not leave a job RUNNING forever
        _recount(job)
        _fail(job, "internal_error", type(exc).__name__)
        raise
    _recount(job)
    if not finished:
        ImportJob.objects.filter(pk=job.pk).update(status=JobStatus.QUEUED, updated_at=timezone.now())
        return RunOutcome("continue")
    _finish(job, None)
    return RunOutcome("done")


def _heartbeat(job: ImportJob, **fields) -> None:
    ImportJob.objects.filter(pk=job.pk).update(heartbeat_at=timezone.now(), updated_at=timezone.now(), **fields)


def _check_cancel(job: ImportJob) -> None:
    if ImportJob.objects.filter(pk=job.pk, cancel_requested=True).exists():
        raise _Cancelled()


def _phase(job: ImportJob) -> str:
    return (job.summary or {}).get("phase", "discover")


def _set_phase(job: ImportJob, phase: str) -> None:
    job.summary = {**(job.summary or {}), "phase": phase}
    ImportJob.objects.filter(pk=job.pk).update(summary=job.summary)


def _run_work(job: ImportJob, provider, deadline: float) -> bool:
    """Returns True when the job's work is complete, False when more slices are needed."""
    if job.job_type in DISCOVERY_TYPES:
        if _phase(job) == "repair":
            _repair_failed_items(job, provider)
            _set_phase(job, "discover")
        if _phase(job) == "discover":
            return _discover(job, provider, deadline)
        return True
    return _by_ids(job, provider, deadline)


def _discover(job: ImportJob, provider, deadline: float) -> bool:
    since = parse_datetime(job.params["since"]) if job.params.get("since") else None
    while True:
        _check_cancel(job)
        page = provider.discover(
            cursor=job.cursor or None,
            page_size=settings.IMPORTS_PAGE_SIZE,
            category=job.params.get("category"),
            since=since,
        )
        for rejected in page.rejected:
            _record_rejected(job, rejected.external_id, rejected.code, rejected.message)
        for warning in page.warnings:
            log(job, LogLevel.WARNING, warning)
        for product in page.items:
            _check_cancel(job)
            _process_item(job, provider, product.external_id, product=product)
        job.cursor = page.next_cursor or ""
        job.n_total = page.total if page.total is not None else job.n_total
        _heartbeat(job, cursor=job.cursor, n_total=job.n_total)
        _recount(job)
        if not page.next_cursor:
            _set_phase(job, "complete")
            return True
        if time.monotonic() >= deadline:
            return False


def _ids_for(job: ImportJob) -> list[str]:
    if job.job_type in (JobType.SINGLE, JobType.BULK):
        return list(job.params["external_ids"])
    if job.job_type == JobType.REPAIR:
        return list(
            ImportJobItem.objects.filter(job_id=job.params["repair_of"], status=ItemStatus.FAILED)
            .exclude(external_id__startswith="invalid:")
            .values_list("external_id", flat=True)
        )
    return []


def _by_ids(job: ImportJob, provider, deadline: float) -> bool:
    if job.job_type == JobType.SYNC:
        last_pk = int(job.cursor or 0)
        qs = ExternalProduct.objects.filter(source=job.source, status=ListingStatus.ACTIVE, pk__gt=last_pk).order_by(
            "pk"
        )
        job.n_total = (
            job.n_total or ExternalProduct.objects.filter(source=job.source, status=ListingStatus.ACTIVE).count()
        )
        while True:
            rows = list(qs.values_list("pk", "external_id")[:CHUNK])
            if not rows:
                return True
            for pk, external_id in rows:
                _check_cancel(job)
                _process_item(job, provider, external_id, fetch=True)
                job.cursor = str(pk)
            qs = qs.filter(pk__gt=int(job.cursor))
            _heartbeat(job, cursor=job.cursor, n_total=job.n_total)
            _recount(job)
            if time.monotonic() >= deadline:
                return False
    ids = _ids_for(job)
    job.n_total = len(ids)
    for external_id in ids:
        _check_cancel(job)
        _process_item(job, provider, external_id, fetch=True)
        if time.monotonic() >= deadline:
            _heartbeat(job, n_total=job.n_total)
            _recount(job)
            return ImportJobItem.objects.filter(job=job).count() >= len(ids)
    _heartbeat(job, n_total=job.n_total)
    return True


def _repair_failed_items(job: ImportJob, provider) -> None:
    failed = list(
        job.items.filter(status=ItemStatus.FAILED)
        .exclude(external_id__startswith="invalid:")
        .values_list("external_id", flat=True)
    )
    for external_id in failed:
        _check_cancel(job)
        _process_item(job, provider, external_id, fetch=True)
    _recount(job)


def _record_rejected(job: ImportJob, external_id: str, code: str, message: str) -> None:
    item, _ = ImportJobItem.objects.get_or_create(job=job, external_id=external_id[:120])
    if item.status == ItemStatus.FAILED or not item.processed_at:
        item.action, item.status, item.error_code, item.error_message = (
            ItemAction.FAILED,
            ItemStatus.FAILED,
            code,
            message[:300],
        )
        item.attempts += 1
        item.processed_at = timezone.now()
        item.save()
        log(job, LogLevel.WARNING, "Provider record rejected.", item=item, code=code)


def _process_item(job: ImportJob, provider, external_id: str, *, product=None, fetch: bool = False) -> None:
    item, _ = ImportJobItem.objects.get_or_create(job=job, external_id=str(external_id)[:120])
    if item.processed_at and item.status in (ItemStatus.SUCCEEDED, ItemStatus.SKIPPED):
        return  # idempotent: finished work is never repeated
    item.attempts += 1
    try:
        if fetch:
            from apps.product_sources.providers.errors import ProviderNotFound

            try:
                product = provider.get_product(external_id)
            except ProviderNotFound:
                _delist_missing(job, item)
                return
        result = process_product(
            product,
            source=job.source,
            data_origin=provider.data_origin,
            gtin_trusted=provider.capabilities.gtin_trusted,
        )
        item.action, item.status = (
            result.action,
            ItemStatus.SKIPPED if result.action == "skipped" else ItemStatus.SUCCEEDED,
        )
        item.external_product, item.error_code, item.error_message = result.ext, "", ""
        item.payload_hash = result.ext.payload_hash if result.ext else ""
        if result.note:
            item.error_message = result.note
    except ItemFailure as exc:
        item.action, item.status, item.error_code, item.error_message = (
            ItemAction.FAILED,
            ItemStatus.FAILED,
            exc.code,
            exc.message[:300],
        )
        log(job, LogLevel.WARNING, "Item failed validation.", item=item, code=exc.code)
    except ProviderError as exc:
        if exc.retryable:
            item.attempts -= 1
            raise  # not the item's fault: the slice stops and the job waits
        item.action, item.status, item.error_code, item.error_message = (
            ItemAction.FAILED,
            ItemStatus.FAILED,
            exc.code,
            str(exc)[:300],
        )
        log(job, LogLevel.WARNING, "Provider error for item.", item=item, code=exc.code)
    except Exception as exc:
        item.action, item.status, item.error_code = ItemAction.FAILED, ItemStatus.FAILED, "internal_error"
        item.error_message = type(exc).__name__
        log(job, LogLevel.ERROR, "Unexpected error while importing an item.", item=item, exc_type=type(exc).__name__)
        _mark_failed_listing(job, str(external_id), "internal_error", type(exc).__name__)
    item.processed_at = timezone.now()
    item.save()


def _delist_missing(job: ImportJob, item: ImportJobItem) -> None:
    from . import canonical as _canonical
    from . import listing  # noqa: F401

    ext = ExternalProduct.objects.filter(source=job.source, external_id=item.external_id).first()
    if ext is None:  # asked for something the source has never shown us: that is a failure, not a delisting
        item.action, item.status, item.error_code = ItemAction.FAILED, ItemStatus.FAILED, "not_found"
        item.error_message = "The source does not list this product."
        item.processed_at = timezone.now()
        item.save()
        log(job, LogLevel.WARNING, "Requested product not found at the source.", item=item)
        return
    now = timezone.now()
    with transaction.atomic():
        listing.mark_delisted(ext, now)
        for ev in ext.variants.exclude(canonical_variant=None).select_related("canonical_variant"):
            _canonical.refresh_variant_availability(ev.canonical_variant)
    item.external_product = ext
    item.action, item.status, item.error_code = ItemAction.DELISTED, ItemStatus.SUCCEEDED, ""
    item.processed_at = timezone.now()
    item.save()
    log(job, LogLevel.INFO, "Source no longer lists the product.", item=item)


def _mark_failed_listing(job: ImportJob, external_id: str, code: str, message: str) -> None:
    from apps.product_sources.models import PipelineStatus as S

    from .statuses import ALLOWED

    ext = ExternalProduct.objects.filter(source=job.source, external_id=external_id).first()
    if ext is not None and S.FAILED in ALLOWED.get(ext.pipeline_status, set()):
        ext.pipeline_status, ext.error_code, ext.error_message = S.FAILED, code, message[:500]
        ext.save(update_fields=["pipeline_status", "error_code", "error_message", "updated_at"])


# ------------------------------------------------------------------------------------ completion
def _recount(job: ImportJob) -> None:
    rows = job.items.values("action", "status").annotate(n=Count("id"))
    counts = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    seen = 0
    for row in rows:
        seen += row["n"]
        if row["status"] == ItemStatus.FAILED:
            counts["failed"] += row["n"]
        elif row["action"] == ItemAction.CREATED:
            counts["created"] += row["n"]
        elif row["action"] in (ItemAction.UPDATED, ItemAction.DELISTED):
            counts["updated"] += row["n"]
        elif row["action"] == ItemAction.UNCHANGED:
            counts["unchanged"] += row["n"]
        else:
            counts["skipped"] += row["n"]
    ImportJob.objects.filter(pk=job.pk).update(
        n_seen=seen,
        n_created=counts["created"],
        n_updated=counts["updated"],
        n_unchanged=counts["unchanged"],
        n_skipped=counts["skipped"],
        n_failed=counts["failed"],
    )
    job.refresh_from_db()


def _finish(job: ImportJob, forced: str | None) -> None:
    job.refresh_from_db()
    _recount(job)
    if forced:
        status = forced
    elif job.n_failed:
        status = JobStatus.PARTIALLY_FAILED if job.n_processed - job.n_failed > 0 else JobStatus.FAILED
    else:
        status = JobStatus.SUCCEEDED
    job.status, job.finished_at, job.resume_at = status, timezone.now(), None
    job.summary = build_summary(job)
    job.save()
    log(
        job,
        LogLevel.ERROR if status == JobStatus.FAILED else LogLevel.INFO,
        f"Job {status}.",
        **{k: v for k, v in job.summary.items() if isinstance(v, (int, float, str))},
    )


def _fail(job: ImportJob, code: str, message: str) -> None:
    job.refresh_from_db()
    job.error_code, job.error_message = code, message[:500]
    job.status, job.finished_at = JobStatus.FAILED, timezone.now()
    job.summary = build_summary(job)
    job.save()
    log(job, LogLevel.ERROR, "Job failed.", code=code)


def _wait(job: ImportJob, exc: ProviderError) -> RunOutcome:
    job.refresh_from_db()
    rate_limited = exc.code == "rate_limited"
    if not rate_limited:
        job.attempt += 1
        if job.attempt > settings.IMPORTS_MAX_ATTEMPTS:
            _fail(job, "retries_exhausted", f"{exc.code}: {exc}")
            return RunOutcome("done")
    delay = float(exc.retry_after) if exc.retry_after else _backoff(job.attempt)
    job.status, job.error_code, job.error_message = JobStatus.WAITING, exc.code, str(exc)[:500]
    job.resume_at = timezone.now() + timedelta(seconds=delay)
    job.save()
    log(
        job,
        LogLevel.WARNING,
        "Waiting before retrying.",
        code=exc.code,
        retry_in_seconds=round(delay),
        attempt=job.attempt,
    )
    return RunOutcome("retry", delay)


def build_summary(job: ImportJob) -> dict:
    end = job.finished_at or timezone.now()
    errors = dict(
        job.items.filter(status=ItemStatus.FAILED).values_list("error_code").annotate(n=Count("id")).order_by("-n")
    )
    held = (
        ExternalProduct.objects.filter(pk__in=job.items.exclude(external_product=None).values("external_product"))
        .values("pipeline_status")
        .annotate(n=Count("id"))
    )
    return {
        "phase": (job.summary or {}).get("phase", ""),
        "seen": job.n_seen,
        "created": job.n_created,
        "updated": job.n_updated,
        "unchanged": job.n_unchanged,
        "skipped": job.n_skipped,
        "failed": job.n_failed,
        "duration_seconds": round((end - job.started_at).total_seconds(), 1) if job.started_at else 0,
        "errors_by_code": errors,
        "products_by_status": {r["pipeline_status"]: r["n"] for r in held},
        "warnings_logged": ImportLog.objects.filter(job=job, level=LogLevel.WARNING).count(),
        "retries": job.retry_count,
    }


# ------------------------------------------------------------------------------------ schedules
def run_scheduled(now=None) -> list[int]:
    now = now or timezone.now()
    created = []
    for code, cfg in (settings.IMPORTS_SCHEDULES or {}).items():
        source = ProductSource.objects.filter(code=code, is_active=True).first()
        if source is None:
            continue
        window = int(now.timestamp() // (int(cfg["every_minutes"]) * 60))
        key = f"sched:{code}:{cfg['job_type']}:{window}"
        try:
            job, was_created = create_job(
                source=source,
                job_type=cfg["job_type"],
                params=cfg.get("params"),
                idempotency_key=key,
                trigger=JobTrigger.SCHEDULED,
            )
        except DomainError:
            continue  # not authorised, or the previous run is still going: skip this window
        if was_created:
            created.append(job.pk)
    return created


def purge_logs(now=None) -> int:
    cutoff = (now or timezone.now()) - timedelta(days=settings.IMPORTS_LOG_RETENTION_DAYS)
    deleted, _ = ImportLog.objects.filter(created_at__lt=cutoff).delete()
    return deleted
