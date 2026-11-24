from celery import shared_task

from apps.common.tasks import MaralTask

from .services import stock


@shared_task(base=MaralTask, name="inventory.expire_reservations")
def expire_reservations() -> int:
    return stock.expire_due()
