"""Celery entry points. Heavy lifting is in services/ so it can be tested without a broker."""

import structlog
from celery import shared_task

from apps.common.tasks import MaralTask

from .services import jobs, ports, readiness

logger = structlog.get_logger("maral.imports")


@shared_task(base=MaralTask, bind=True, name="imports.run_job")
def run_job(self, job_id: int) -> str:
    """One time-boxed slice of a job; re-queues itself until the job is finished."""
    try:
        outcome = jobs.run_slice(job_id)
    except Exception:
        raise
    if outcome.state == "continue":
        run_job.apply_async(args=[job_id])
    elif outcome.state == "retry":
        run_job.apply_async(args=[job_id], countdown=max(1, int(outcome.retry_after or 60)))
    return outcome.state


@shared_task(base=MaralTask, name="imports.run_scheduled")
def run_scheduled() -> list[int]:
    return jobs.run_scheduled()


@shared_task(base=MaralTask, name="imports.recover_stale")
def recover_stale() -> list[int]:
    return jobs.recover_stale()


@shared_task(base=MaralTask, name="imports.purge_logs")
def purge_logs() -> int:
    return jobs.purge_logs()


@shared_task(base=MaralTask, name="imports.refresh_readiness")
def refresh_readiness() -> int:
    from apps.product_sources.models import ExternalProduct
    from apps.product_sources.models import PipelineStatus as S

    n = 0
    for ext in ExternalProduct.objects.filter(
        pipeline_status__in=[S.IMPORTED, S.TRANSLATION_PENDING, S.PRICING_PENDING, S.READY_TO_PUBLISH],
        canonical_product__isnull=False,
    ).select_related("canonical_product"):
        before = ext.pipeline_status
        if readiness.refresh_readiness(ext) != before:
            n += 1
    return n


@shared_task(base=MaralTask, name="imports.request_translation")
def request_translation(ext_id: int) -> dict:
    from apps.product_sources.models import ExternalProduct

    ext = ExternalProduct.objects.select_related("canonical_product").filter(pk=ext_id).first()
    result = ports.dispatch_translation(ext) if ext else {"queued": False, "reason": "missing"}
    logger.info("import_translation_request", ext=ext_id, **result)
    return result


@shared_task(base=MaralTask, name="imports.request_pricing")
def request_pricing(ext_id: int) -> dict:
    from apps.product_sources.models import ExternalProduct

    ext = ExternalProduct.objects.select_related("canonical_product").filter(pk=ext_id).first()
    result = ports.dispatch_pricing(ext) if ext else {"queued": False, "reason": "missing"}
    logger.info("import_pricing_request", ext=ext_id, **result)
    return result
