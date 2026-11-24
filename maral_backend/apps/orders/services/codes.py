from django.db import transaction

from ..models import OrderCodeCounter


@transaction.atomic
def next_code() -> str:
    """MRL-###### from a locked counter row (sequential, collision-free, widens past 6 digits)."""
    OrderCodeCounter.objects.get_or_create(pk=1)
    counter = OrderCodeCounter.objects.select_for_update().get(pk=1)
    counter.last_value += 1
    counter.save(update_fields=["last_value"])
    return f"MRL-{counter.last_value}"
