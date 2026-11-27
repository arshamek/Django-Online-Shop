"""Conversation retention: the only place stored conversations are deleted automatically."""

from datetime import timedelta

from django.utils import timezone

from ..models import AssistantConfig, Conversation


def purge_expired(now=None) -> int:
    now = now or timezone.now()
    config = AssistantConfig.load()
    cutoff = now - timedelta(days=config.retention_days)
    _total, per_model = Conversation.objects.filter(updated_at__lt=cutoff).delete()
    return per_model.get("ai_assistant.Conversation", 0)
