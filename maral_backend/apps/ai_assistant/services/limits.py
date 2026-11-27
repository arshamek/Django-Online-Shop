"""Usage tracking and limits. Per-minute throttling is DRF's (`ai_chat` scope); the daily budget is counted here."""

from datetime import timedelta

from django.utils import timezone

from apps.common.exceptions import DomainError

from ..models import AssistantConfig, Conversation, UsageRecord, UsageStatus

MAX_ACTIVE_CONVERSATIONS = 50


class UsageLimitExceeded(DomainError):
    status_code = 429
    default_code = "ai_usage_limit"
    default_detail = "The daily assistant usage limit has been reached."


def check_daily(user, config: AssistantConfig) -> None:
    since = timezone.now() - timedelta(hours=24)
    used = (
        UsageRecord.objects.filter(user=user, created_at__gte=since)
        .exclude(status__in=(UsageStatus.LIMITED, UsageStatus.DISABLED))
        .count()
    )
    if used >= config.daily_message_limit:
        raise UsageLimitExceeded()


def check_conversation_cap(user) -> None:
    if Conversation.objects.filter(user=user).count() >= MAX_ACTIVE_CONVERSATIONS:
        raise UsageLimitExceeded(
            "Too many stored conversations. Delete an old one first.", code="ai_conversation_limit"
        )


def record(
    user,
    *,
    conversation=None,
    provider="",
    model="",
    demo_mode=False,
    status=UsageStatus.OK,
    error_code="",
    input_tokens=0,
    output_tokens=0,
    latency_ms=0,
    flagged=False,
) -> UsageRecord:
    return UsageRecord.objects.create(
        user=user,
        conversation=conversation,
        provider=provider,
        model=model,
        demo_mode=demo_mode,
        status=status,
        error_code=error_code[:60],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        flagged_content=flagged,
    )
