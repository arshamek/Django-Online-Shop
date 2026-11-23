from celery import shared_task

from apps.common.tasks import MaralTask

from .services import jobs


@shared_task(base=MaralTask, name="translations.run_job")
def run_job(job_id: int) -> str | None:
    job = jobs.run_job(job_id)
    return job.status if job else None


@shared_task(base=MaralTask, name="translations.recover")
def recover() -> dict:
    return jobs.recover()


@shared_task(base=MaralTask, name="translations.enqueue_entity")
def enqueue_entity(entity_type: str, entity_id: int, languages: list[str] | None = None) -> int:
    return len(jobs.enqueue(entity_type, entity_id, languages))
