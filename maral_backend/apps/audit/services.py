"""audit.record(...) - the only supported way to write audit entries."""

from typing import Any

from apps.common.context import get_request_id
from apps.common.logging_config import redact_sensitive
from apps.common.net import get_client_ip

from .models import AuditLog


def _clean_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    cleaned = redact_sensitive(None, "", dict(metadata))
    return {
        k: (v if isinstance(v, (str, int, float, bool, type(None), list, dict)) else str(v)) for k, v in cleaned.items()
    }


def record(action: str, *, request=None, actor=None, target=None, **metadata) -> AuditLog:
    """
    `actor` defaults to the authenticated user of `request`. `target` is a model instance
    (its public_id/pk is stored) or a (type, id) tuple.
    """
    if actor is None and request is not None:
        candidate = getattr(request, "user", None)
        actor = candidate if candidate is not None and getattr(candidate, "is_authenticated", False) else None

    target_type = target_id = ""
    if isinstance(target, tuple):
        target_type, target_id = target[0], str(target[1])
    elif target is not None:
        target_type = target._meta.model_name
        target_id = str(getattr(target, "public_id", target.pk))

    return AuditLog.objects.create(
        action=action,
        actor=actor,
        actor_public_id=getattr(actor, "public_id", None),
        target_type=target_type,
        target_id=target_id,
        ip_address=get_client_ip(request) if request is not None else None,
        user_agent=(request.META.get("HTTP_USER_AGENT", "")[:255] if request is not None else ""),
        request_id=get_request_id() or "",
        metadata=_clean_metadata(metadata),
    )
