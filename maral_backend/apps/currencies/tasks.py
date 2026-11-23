import structlog
from celery import shared_task

from apps.common.tasks import MaralTask

from .models import ExchangeRateSource
from .services import refresh

logger = structlog.get_logger("maral.currencies")


@shared_task(base=MaralTask, name="currencies.refresh_source")
def refresh_source(source_id: int) -> dict:
    source = ExchangeRateSource.objects.filter(pk=source_id, is_enabled=True).first()
    if source is None:
        return {"ok": False, "reason": "missing_or_disabled"}
    log = refresh.refresh_source(source)
    return {"ok": log.ok, "accepted": log.n_accepted, "error": log.error_code}


@shared_task(base=MaralTask, name="currencies.refresh_due")
def refresh_due() -> list[str]:
    done = []
    for source in refresh.due_sources():
        refresh.refresh_source(source)
        done.append(source.code)
    return done
