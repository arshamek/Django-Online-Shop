from celery import shared_task

from .services import shipments


@shared_task(name="shipping.sync_tracking")
def sync_tracking() -> dict:
    return shipments.sync_all()
