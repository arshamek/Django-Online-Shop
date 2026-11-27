from celery import shared_task

from .services import retention


@shared_task(name="ai_assistant.purge_expired_conversations")
def purge_expired_conversations() -> int:
    return retention.purge_expired()
