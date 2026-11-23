"""Idempotent job queue with retry/back-off, stale-lock recovery and a dead-letter state."""

from datetime import timedelta

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.common.languages import SUPPORTED_LANGUAGES

from ..errors import TranslationProviderError, TranslationValidationError
from ..models import JobStatus, TranslationJob
from .entities import adapter_for
from .translate import translate_entity

logger = structlog.get_logger("maral.translations")
BACKOFF_MINUTES = (1, 2, 5, 15, 30, 60)


def backoff(attempts: int) -> timedelta:
    return timedelta(minutes=BACKOFF_MINUTES[min(max(attempts, 1), len(BACKOFF_MINUTES)) - 1])


def _dispatch(job_id: int, countdown: int = 0) -> None:
    from .. import tasks

    tasks.run_job.apply_async(args=[job_id], countdown=countdown, queue="translations")


def enqueue(
    entity_type: str, entity_id: int, languages=None, *, force: bool = False, requested_by=None, dispatch: bool = True
):
    """One job per (entity, language, source text). Re-requesting is a no-op unless `force` or the source text changed."""
    adapter = adapter_for(entity_type)
    loaded = adapter.load(entity_id)
    if loaded is None:
        return []
    jobs = []
    for target in languages or [c for c in SUPPORTED_LANGUAGES if c != loaded.source_language]:
        if target == loaded.source_language or target not in SUPPORTED_LANGUAGES:
            continue
        job, created = TranslationJob.objects.get_or_create(
            entity_type=entity_type,
            entity_id=entity_id,
            target_language=target,
            source_text_hash=loaded.source_hash,
            defaults={"source_language": loaded.source_language, "requested_by": requested_by},
        )
        if not created and force and job.status != JobStatus.RUNNING:
            reset(job, save=True)
        elif not created:
            continue
        jobs.append(job)
        if dispatch:
            transaction.on_commit(lambda pk=job.pk: _dispatch(pk))
    return jobs


def reset(job: TranslationJob, save: bool = False) -> None:
    job.status, job.attempts, job.next_attempt_at = JobStatus.QUEUED, 0, timezone.now()
    job.error_code = job.error_message = ""
    job.locked_at = job.finished_at = None
    if save:
        job.save()


def claim(job_id: int, now=None) -> TranslationJob | None:
    now = now or timezone.now()
    with transaction.atomic():
        job = TranslationJob.objects.select_for_update().filter(pk=job_id).first()
        if job is None or job.status not in (JobStatus.QUEUED, JobStatus.FAILED) or job.next_attempt_at > now:
            return None
        job.status, job.locked_at, job.started_at, job.attempts = (
            JobStatus.RUNNING,
            now,
            job.started_at or now,
            job.attempts + 1,
        )
        job.save(update_fields=["status", "locked_at", "started_at", "attempts", "updated_at"])
        return job


def run_job(job_id: int, *, provider=None, now=None) -> TranslationJob | None:
    job = claim(job_id, now)
    if job is None:
        return None
    finished = timezone.now()
    try:
        result = translate_entity(job.entity_type, job.entity_id, job.target_language, provider=provider)
    except TranslationProviderError as exc:
        _failed(job, exc.code, exc.message, retry=exc.transient, finished=finished)
    except TranslationValidationError as exc:
        _failed(job, exc.code, exc.message, retry=True, finished=finished)  # a different answer may pass next time
    except Exception as exc:  # noqa: BLE001 - a bug must end in a visible failed job, not a lost one
        logger.error("translation_job_crashed", job=job.pk, exc_type=type(exc).__name__)
        _failed(job, "internal_error", type(exc).__name__, retry=True, finished=finished)
    else:
        job.status = JobStatus.SKIPPED if result["outcome"] == "skipped" else JobStatus.SUCCEEDED
        job.provider, job.model = result.get("provider", ""), result.get("model", "")
        job.units_total, job.units_from_cache, job.chars_sent = (
            result.get("units", 0),
            result.get("from_cache", 0),
            result.get("chars", 0),
        )
        job.result, job.error_code, job.error_message, job.locked_at, job.finished_at = (
            result,
            "",
            "",
            None,
            timezone.now(),
        )
        job.save()
    return job


def _failed(job, code, message, *, retry, finished):
    exhausted = job.attempts >= settings.TRANSLATION_MAX_ATTEMPTS
    job.error_code, job.error_message, job.locked_at, job.finished_at = code[:60], message[:300], None, finished
    if not retry or exhausted:
        job.status = JobStatus.DEAD
    else:
        job.status, job.next_attempt_at = JobStatus.FAILED, finished + backoff(job.attempts)
    job.save()
    logger.warning("translation_job_failed", job=job.pk, code=code, status=job.status, attempts=job.attempts)
    if job.status == JobStatus.FAILED:
        transaction.on_commit(lambda: _dispatch(job.pk, countdown=int(backoff(job.attempts).total_seconds())))


def recover(now=None) -> dict:
    """Run periodically: free stuck jobs, re-dispatch due retries and lost messages."""
    now = now or timezone.now()
    counts = {"unlocked": 0, "dead": 0, "redispatched": 0}
    stale_before = now - timedelta(minutes=settings.TRANSLATION_STALE_JOB_MINUTES)
    for job in TranslationJob.objects.filter(status=JobStatus.RUNNING, locked_at__lt=stale_before):
        if job.attempts >= settings.TRANSLATION_MAX_ATTEMPTS:
            job.status, job.error_code, job.locked_at = JobStatus.DEAD, "worker_lost", None
            counts["dead"] += 1
        else:
            job.status, job.locked_at, job.next_attempt_at, job.error_code = JobStatus.FAILED, None, now, "worker_lost"
            counts["unlocked"] += 1
            transaction.on_commit(lambda pk=job.pk: _dispatch(pk))
        job.save()
    due = TranslationJob.objects.filter(
        status__in=[JobStatus.QUEUED, JobStatus.FAILED],
        next_attempt_at__lte=now,
        updated_at__lt=now - timedelta(minutes=1),
    )
    for job in due[:500]:
        _dispatch(job.pk)
        counts["redispatched"] += 1
    return counts


def requeue(job: TranslationJob) -> TranslationJob:
    if job.status == JobStatus.RUNNING:
        return job
    reset(job, save=True)
    transaction.on_commit(lambda: _dispatch(job.pk))
    return job
