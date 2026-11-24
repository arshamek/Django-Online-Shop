from celery import shared_task

from apps.common.tasks import MaralTask

from .services import lifecycle


@shared_task(base=MaralTask, name="orders.expire_unpaid")
def expire_unpaid() -> int:
    """Cancels orders whose payment window ended and releases their stock. Idempotent."""
    return lifecycle.expire_unpaid()


@shared_task(base=MaralTask, name="orders.expire_checkouts")
def expire_checkouts() -> int:
    return lifecycle.expire_checkouts()
