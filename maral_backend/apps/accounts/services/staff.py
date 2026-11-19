"""Staff operations on customer accounts. Every action is audited."""

from rest_framework.exceptions import PermissionDenied

from apps.audit import services as audit
from apps.common.exceptions import BusinessRuleViolation

from ..models import AccountStatus, SessionEnd, User
from . import sessions


def _guard(actor: User, target: User) -> None:
    if target.pk == actor.pk:
        raise BusinessRuleViolation("You cannot change your own account state.", code="self_action_forbidden")
    if (target.is_staff or target.is_superuser) and not actor.is_superuser:
        raise PermissionDenied("Only a Super Admin can act on staff accounts.", "permission_denied")


def suspend(request, actor: User, target: User, reason: str) -> User:
    _guard(actor, target)
    target.status = AccountStatus.SUSPENDED
    target.save(update_fields=["status"])
    revoked = sessions.revoke_user_sessions(target, SessionEnd.STAFF)
    audit.record(
        "staff.customer.suspended", request=request, actor=actor, target=target, reason=reason, revoked_sessions=revoked
    )
    return target


def reactivate(request, actor: User, target: User) -> User:
    _guard(actor, target)
    target.status = AccountStatus.ACTIVE
    target.save(update_fields=["status"])
    audit.record("staff.customer.reactivated", request=request, actor=actor, target=target)
    return target


def unlock(request, actor: User, target: User) -> User:
    _guard(actor, target)
    target.failed_login_count = 0
    target.locked_until = None
    target.save(update_fields=["failed_login_count", "locked_until"])
    audit.record("staff.customer.unlocked", request=request, actor=actor, target=target)
    return target


def revoke_sessions(request, actor: User, target: User) -> int:
    _guard(actor, target)
    revoked = sessions.revoke_user_sessions(target, SessionEnd.STAFF)
    audit.record(
        "staff.customer.sessions_revoked", request=request, actor=actor, target=target, revoked_sessions=revoked
    )
    return revoked
