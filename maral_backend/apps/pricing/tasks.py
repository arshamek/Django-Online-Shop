import structlog
from celery import shared_task
from django.db.models import F

from apps.common.tasks import MaralTask

from .models import PriceCause
from .services import quotes, repricing

logger = structlog.get_logger("maral.pricing")
CHUNK = 200


@shared_task(base=MaralTask, name="pricing.reprice_variants")
def reprice_variants(variant_ids: list[int], cause: str = PriceCause.MANUAL) -> dict:
    return repricing.reprice_variants(variant_ids, cause=cause)


@shared_task(base=MaralTask, name="pricing.reprice_currency")
def reprice_currency(currencies: list[str]) -> int:
    ids = repricing.variants_for_currencies(currencies)
    for i in range(0, len(ids), CHUNK):
        reprice_variants.delay(ids[i : i + CHUNK], cause=PriceCause.RATE_CHANGE)
    return len(ids)


@shared_task(base=MaralTask, name="pricing.reprice_all")
def reprice_all(cause: str = PriceCause.RULE_CHANGE, version: int | None = None) -> int:
    from apps.product_sources.models import ExternalProductVariant

    ids = list(
        ExternalProductVariant.objects.filter(
            is_active=True, canonical_variant__isnull=False, current_price__isnull=False
        )
        .values_list("canonical_variant_id", flat=True)
        .distinct()
    )
    for i in range(0, len(ids), CHUNK):
        reprice_variants.delay(ids[i : i + CHUNK], cause=cause)
    return len(ids)


@shared_task(base=MaralTask, name="pricing.reprice_changed_sources")
def reprice_changed_sources() -> int:
    """Source price observed after our last calculation (or never priced) -> reprice."""
    from apps.product_sources.models import ExternalProductVariant

    changed = (
        ExternalProductVariant.objects.filter(
            is_active=True,
            canonical_variant__isnull=False,
            current_price__isnull=False,
            canonical_variant__price__computed_at__lt=F("current_price__observed_at"),
        )
        .values_list("canonical_variant_id", flat=True)
        .distinct()
    )
    ids = list(changed)
    for i in range(0, len(ids), CHUNK):
        reprice_variants.delay(ids[i : i + CHUNK], cause=PriceCause.SOURCE_PRICE_CHANGE)
    return len(ids)


@shared_task(base=MaralTask, name="pricing.expire_quotes")
def expire_quotes() -> int:
    n = quotes.expire_due()
    if n:
        logger.info("quotes_expired", count=n)
    return n
