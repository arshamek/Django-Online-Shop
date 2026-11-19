from django.utils import timezone

from apps.audit import services as audit

from ..models import SessionEnd, UserSession


def active_sessions(user):
    return UserSession.objects.filter(user=user, revoked_at__isnull=True)


def revoke(session: UserSession, reason: str) -> None:
    if session.revoked_at is None:
        session.revoked_at = timezone.now()
        session.revoked_reason = reason
        session.save(update_fields=["revoked_at", "revoked_reason"])


def revoke_user_sessions(user, reason: str, *, keep: UserSession | None = None) -> int:
    qs = active_sessions(user)
    if keep is not None:
        qs = qs.exclude(pk=keep.pk)
    return qs.update(revoked_at=timezone.now(), revoked_reason=reason)


def revoke_one(request, user, public_id, *, current: UserSession | None) -> bool:
    """Revoke one of the user's own sessions. Returns False when it does not exist (-> 404)."""
    session = active_sessions(user).filter(public_id=public_id).first()
    if session is None:
        return False
    revoke(session, SessionEnd.USER)
    audit.record(
        "auth.session.revoked",
        request=request,
        target=user,
        session=str(session.public_id),
        current=bool(current and current.pk == session.pk),
    )
    return True
