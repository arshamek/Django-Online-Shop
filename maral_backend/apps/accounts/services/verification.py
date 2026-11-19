"""Email (signed link) and phone (one-time code) verification. No provider is assumed to exist."""

import hashlib
import hmac
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import Throttled, ValidationError

from apps.audit import services as audit
from apps.common.exceptions import BusinessRuleViolation, Conflict, DomainError, UpstreamUnavailable
from apps.common.transactions import dispatch_task

from .. import messages, tasks
from ..delivery import ProviderUnavailable, send_sms
from ..models import PhoneVerificationCode, User

EMAIL_RESEND_SECONDS = 60


class OTPError(DomainError):
    status_code = 400  # not 401: the caller IS authenticated, a 401 would look like an expired session
    default_code = "otp_invalid"
    default_detail = "The verification code is incorrect."


# ---- email ---------------------------------------------------------------------------------
def request_email_verification(request, user: User) -> None:
    if not user.email:
        raise BusinessRuleViolation("Add an email address first.", code="no_email")
    if user.email_verified:
        raise Conflict("Email already verified.", code="already_verified")
    if not cache.add(f"email-verify-sent:{user.pk}", 1, timeout=EMAIL_RESEND_SECONDS):
        raise Throttled(wait=EMAIL_RESEND_SECONDS)
    audit.record("account.email.verification_requested", request=request, target=user)
    dispatch_task(tasks.send_email_verification, user.pk)


def confirm_email(request, token: str) -> User:
    try:
        data = signing.loads(token, salt=tasks.EMAIL_VERIFY_SALT, max_age=settings.EMAIL_VERIFICATION_MAX_AGE)
        user = User.objects.filter(public_id=uuid.UUID(hex=data["u"])).first()
    except (signing.BadSignature, KeyError, ValueError, TypeError):
        user = None
        data = {}
    if user is None or user.email != data.get("e"):
        audit.record("account.email.verification_invalid", request=request, target=user)
        raise ValidationError({"token": ["This verification link is invalid or has expired."]}, code="invalid_token")
    if user.email_verified_at is None:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at"])
        audit.record("account.email.verified", request=request, actor=user, target=user)
    return user


# ---- phone ---------------------------------------------------------------------------------
def _hash_code(user: User, phone: str, code: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(), f"phone-otp:{user.pk}:{phone}:{code}".encode(), hashlib.sha256
    ).hexdigest()


def request_phone_code(request, user: User) -> None:
    if not user.phone:
        raise BusinessRuleViolation("Add a phone number first.", code="no_phone")
    if user.phone_verified:
        raise Conflict("Phone already verified.", code="already_verified")

    now = timezone.now()
    window_start = now - timedelta(seconds=settings.PHONE_OTP_REQUEST_WINDOW)
    recent = list(
        PhoneVerificationCode.objects.filter(phone=user.phone, created_at__gte=window_start).order_by("created_at")
    )
    if len(recent) >= settings.PHONE_OTP_MAX_REQUESTS:
        wait = int((recent[0].created_at + timedelta(seconds=settings.PHONE_OTP_REQUEST_WINDOW) - now).total_seconds())
        audit.record("account.phone.otp_throttled", request=request, target=user)
        raise Throttled(wait=max(wait, 1))

    code = f"{secrets.randbelow(10**6):06d}"
    with transaction.atomic():
        PhoneVerificationCode.objects.filter(user=user, phone=user.phone, consumed_at__isnull=True).update(
            consumed_at=now
        )
        row = PhoneVerificationCode.objects.create(
            user=user,
            phone=user.phone,
            code_hash=_hash_code(user, user.phone, code),
            expires_at=now + timedelta(seconds=settings.PHONE_OTP_TTL),
        )
        language = getattr(getattr(user, "profile", None), "language", "fa")
        try:
            send_sms(user.phone, messages.render("phone_otp", language, code=code)[1])
        except ProviderUnavailable as exc:
            raise UpstreamUnavailable("Phone verification is currently unavailable.", code="sms_unavailable") from exc
    audit.record("account.phone.otp_requested", request=request, target=user, code_id=row.pk)


def confirm_phone_code(request, user: User, code: str) -> User:
    now = timezone.now()
    row = (
        PhoneVerificationCode.objects.filter(user=user, phone=user.phone or "", consumed_at__isnull=True)
        .order_by("-created_at")
        .first()
    )
    if row is None:
        raise OTPError("Request a new verification code.", code="otp_invalid")
    if row.expires_at <= now:
        row.consumed_at = now
        row.save(update_fields=["consumed_at"])
        raise OTPError("The verification code has expired.", code="otp_expired")
    PhoneVerificationCode.objects.filter(pk=row.pk).update(attempts=F("attempts") + 1)
    row.refresh_from_db()
    if row.attempts > settings.PHONE_OTP_MAX_ATTEMPTS:
        row.consumed_at = now
        row.save(update_fields=["consumed_at"])
        audit.record("account.phone.otp_exhausted", request=request, target=user)
        raise OTPError("Too many incorrect attempts. Request a new code.", code="otp_invalid")
    if not hmac.compare_digest(row.code_hash, _hash_code(user, row.phone, code)):
        audit.record("account.phone.otp_failed", request=request, target=user)
        raise OTPError()
    with transaction.atomic():
        row.consumed_at = now
        row.save(update_fields=["consumed_at"])
        user.phone_verified_at = now
        user.save(update_fields=["phone_verified_at"])
        audit.record("account.phone.verified", request=request, target=user)
    return user
