"""
Task conventions
----------------
* Always use ``@shared_task(base=MaralTask, name="<app>.<task>")`` - explicit names survive refactors.
* Pass ids, never PII/secrets, as arguments: ``CELERY_RESULT_EXTENDED`` stores args in the result table.
* Tasks must be idempotent (``acks_late`` means a task can run twice after a worker crash).
* Raise ``TransientError`` (or ConnectionError/TimeoutError) for retryable failures; anything else fails at once.
"""

import structlog
from celery import Task, shared_task
from django.core.cache import cache
from django.utils import timezone

from .exceptions import TransientError

logger = structlog.get_logger("maral.tasks")

HEARTBEAT_CACHE_KEY = "celery:beat:heartbeat"


class MaralTask(Task):
    autoretry_for = (TransientError, ConnectionError, TimeoutError)
    retry_backoff = 2  # 2s, 4s, 8s, ...
    retry_backoff_max = 600
    retry_jitter = True
    max_retries = 5

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        logger.warning("task_retry", task=self.name, retries=self.request.retries, exc_type=type(exc).__name__)

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        # Arguments are deliberately not logged. The failed run is also stored (status FAILURE,
        # traceback, worker) in django_celery_results.TaskResult for review.
        logger.error("task_failed", task=self.name, exc_type=type(exc).__name__, exc_info=exc)


@shared_task(base=MaralTask, name="common.ping")
def ping() -> dict:
    """Simple test task: proves broker -> worker -> result backend works."""
    return {"pong": True, "at": timezone.now().isoformat()}


@shared_task(base=MaralTask, name="common.heartbeat")
def heartbeat() -> str:
    """Scheduled by beat every 5 minutes; the cache key shows beat + worker are alive."""
    now = timezone.now().isoformat()
    cache.set(HEARTBEAT_CACHE_KEY, now, timeout=1800)
    logger.info("beat_heartbeat")
    return now


@shared_task(base=MaralTask, bind=True, name="common.debug_fail")
def debug_fail(self, mode: str = "permanent", fail_times: int = 2) -> str:
    """
    Verification aid for retry behaviour and failed-task tracking - not used by any workflow.
    mode="permanent": fails immediately (no retry, recorded as FAILURE).
    mode="transient": raises TransientError `fail_times` times, then succeeds (exercises backoff).
    """
    if mode == "transient":
        if self.request.retries < fail_times:
            raise TransientError(f"simulated transient failure #{self.request.retries + 1}")
        return f"succeeded after {self.request.retries} retries"
    raise ValueError("simulated permanent failure")
