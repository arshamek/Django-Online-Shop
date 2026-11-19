import structlog
from django.db import transaction

logger = structlog.get_logger("maral.tasks")


def dispatch_task(task, *args, **kwargs) -> None:
    """Queue a Celery task after the surrounding transaction commits. Broker outages never fail the request."""

    def _send():
        try:
            task.delay(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - best effort; the user can retry the action
            logger.error("task_dispatch_failed", task=getattr(task, "name", str(task)), exc_type=type(exc).__name__)

    transaction.on_commit(_send)
