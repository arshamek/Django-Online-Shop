from django.contrib.auth.models import Group, Permission
from django.contrib.auth.signals import user_logged_in
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from apps.audit import services as audit
from apps.common.net import get_client_ip

from .models import User, UserSession

SESSION_SID_KEY = "sid"


@receiver(user_logged_in)
def create_user_session(sender, request, user, **kwargs):
    """Every login (API, admin, tests) gets a server-side UserSession that can be revoked."""
    if request is None or not hasattr(request, "session"):
        return
    session = UserSession.objects.create(
        user=user,
        ip_address=get_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
    )
    request.session[SESSION_SID_KEY] = str(session.public_id)


@receiver(m2m_changed, sender=User.groups.through)
@receiver(m2m_changed, sender=User.user_permissions.through)
def audit_role_changes(sender, instance, action, pk_set, reverse, **kwargs):
    if action not in {"post_add", "post_remove", "post_clear"} or reverse or not isinstance(instance, User):
        return
    kind = "groups" if sender is User.groups.through else "permissions"
    model = Group if kind == "groups" else Permission
    names = sorted(model.objects.filter(pk__in=pk_set or []).values_list("name", flat=True))
    audit.record(f"staff.{kind}.{action.removeprefix('post_')}", target=instance, **{kind: names})
