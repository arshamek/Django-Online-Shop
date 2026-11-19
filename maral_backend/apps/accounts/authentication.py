from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import AuthenticationFailed

from apps.common.api.authentication import SessionAuthentication401

from .models import UserSession
from .signals import SESSION_SID_KEY

LAST_SEEN_RESOLUTION = timedelta(minutes=5)


class ManagedSessionAuthentication(SessionAuthentication401):
    """
    Cookie session auth + server-side session record. A session that was revoked (logout elsewhere,
    password change/reset, staff action) or exceeded the absolute lifetime is rejected immediately
    with `session_expired` and its cookie data is dropped. `request.auth` is the UserSession.
    """

    def authenticate(self, request):
        result = super().authenticate(request)  # session user + CSRF check for unsafe methods
        if result is None:
            return None
        user, _unused = result
        sid = request.session.get(SESSION_SID_KEY)
        record = None
        if sid:
            try:
                record = UserSession.objects.filter(public_id=sid, user=user).first()
            except (ValueError, TypeError):
                record = None
        now = timezone.now()
        expired = (
            record is None
            or record.revoked_at is not None
            or (now - record.created_at > timedelta(seconds=settings.SESSION_ABSOLUTE_AGE))
        )
        if expired:
            request._request.session.flush()
            raise AuthenticationFailed(_("Your session has expired. Please sign in again."), code="session_expired")
        if now - record.last_seen_at > LAST_SEEN_RESOLUTION:
            UserSession.objects.filter(pk=record.pk).update(last_seen_at=now)
        return user, record
