"""
Email tasks. Arguments are user ids only (task arguments are stored in the result table);
tokens are generated inside the task and never persisted.
"""

import smtplib

import structlog
from celery import shared_task
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.core.mail import send_mail

from apps.common.exceptions import TransientError
from apps.common.tasks import MaralTask

from . import messages
from .models import User

logger = structlog.get_logger("maral.accounts")
EMAIL_VERIFY_SALT = "accounts.email-verification"


def make_email_verification_token(user: User) -> str:
    # Bound to the current address: changing the email invalidates older links.
    return signing.dumps({"u": user.public_id.hex, "e": user.email}, salt=EMAIL_VERIFY_SALT)


def _language(user: User) -> str:
    profile = getattr(user, "profile", None)
    return profile.language if profile else "fa"


def _send(user: User, template: str, **values) -> None:
    subject, body = messages.render(template, _language(user), **values)
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
    except (smtplib.SMTPException, OSError) as exc:
        raise TransientError(f"email delivery failed: {type(exc).__name__}") from exc


@shared_task(base=MaralTask, name="accounts.send_password_reset_email")
def send_password_reset_email(user_id: int) -> None:
    user = User.objects.filter(pk=user_id).first()
    if user is None or not user.email or not user.is_active:
        return
    token = default_token_generator.make_token(user)
    link = f"{settings.FRONTEND_BASE_URL}/reset-password?uid={user.public_id.hex}&token={token}"
    _send(user, "password_reset", link=link)
    logger.info("password_reset_email_sent", user=str(user.public_id))


@shared_task(base=MaralTask, name="accounts.send_email_verification")
def send_email_verification(user_id: int) -> None:
    user = User.objects.filter(pk=user_id).first()
    if user is None or not user.email or user.email_verified or not user.is_active:
        return
    link = f"{settings.FRONTEND_BASE_URL}/verify-email?token={make_email_verification_token(user)}"
    _send(user, "email_verification", link=link)
    logger.info("email_verification_sent", user=str(user.public_id))


@shared_task(base=MaralTask, name="accounts.send_password_changed_notice")
def send_password_changed_notice(user_id: int) -> None:
    user = User.objects.filter(pk=user_id).first()
    if user is None or not user.email:
        return
    _send(user, "password_changed")


@shared_task(base=MaralTask, name="accounts.cleanup_expired")
def cleanup_expired() -> dict:
    """Daily housekeeping: drop dead auth-session records and stale phone codes (no audit rows are touched)."""
    from datetime import timedelta

    from django.utils import timezone

    from .models import PhoneVerificationCode, UserSession

    now = timezone.now()
    sessions, _ = UserSession.objects.filter(
        created_at__lt=now - timedelta(seconds=settings.SESSION_ABSOLUTE_AGE + 7 * 86400)
    ).delete()
    revoked, _ = UserSession.objects.filter(revoked_at__lt=now - timedelta(days=30)).delete()
    codes, _ = PhoneVerificationCode.objects.filter(created_at__lt=now - timedelta(days=1)).delete()
    logger.info("accounts_cleanup", sessions=sessions + revoked, phone_codes=codes)
    return {"sessions": sessions + revoked, "phone_codes": codes}
