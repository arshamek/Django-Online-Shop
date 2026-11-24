from celery import shared_task
from django.utils import timezone

from apps.common.tasks import MaralTask

from .models import Cart, CartStatus


@shared_task(base=MaralTask, name="cart.purge_stale")
def purge_stale() -> int:
    """Delete expired guest carts (their lines cascade). Customer carts are kept."""
    qs = Cart.objects.filter(user__isnull=True, status=CartStatus.ACTIVE, expires_at__lte=timezone.now())
    count = qs.count()
    qs.delete()
    return count
