"""Password change/reset and sign-in identifier changes."""

import hashlib
import uuid

from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit import services as audit
from apps.common.exceptions import Conflict
from apps.common.transactions import dispatch_task

from .. import tasks
from ..models import SessionEnd, User, UserSession
from ..normalizers import looks_like_email, normalize_email
from . import sessions
from .auth import password_error_messages

RESET_REQUESTS_PER_HOUR = 3


def _finish_password_change(user: User) -> None:
    user.password_changed_at = timezone.now()
    user.failed_login_count = 0
    user.locked_until = None
    user.save(update_fields=["password", "password_changed_at", "failed_login_count", "locked_until"])


def change_password(
    request, user: User, current_session: UserSession, *, current_password: str, new_password: str
) -> None:
    if not user.check_password(current_password):
        audit.record("auth.password.change_failed", request=request, target=user)
        raise ValidationError({"current_password": ["Incorrect password."]}, code="invalid_password")
    if current_password == new_password:
        raise ValidationError({"new_password": ["The new password must differ from the current one."]})
    errors = password_error_messages(new_password, user)
    if errors:
        raise ValidationError({"new_password": errors})
    with transaction.atomic():
        user.set_password(new_password)
        _finish_password_change(user)
        update_session_auth_hash(request, user)  # keep THIS session valid (rotates its key)
        revoked = sessions.revoke_user_sessions(user, SessionEnd.PASSWORD_CHANGE, keep=current_session)
        audit.record("auth.password.changed", request=request, target=user, revoked_sessions=revoked)
        dispatch_task(tasks.send_password_changed_notice, user.pk)


def request_password_reset(request, identifier: str) -> None:
    """Always behaves the same to the caller; only emails an existing, active account with an email."""
    canonical = None
    user = None
    if looks_like_email(identifier):
        try:
            canonical = normalize_email(identifier)
        except Exception:  # noqa: BLE001 - malformed -> treated as unknown
            canonical = None
        if canonical:
            user = User.objects.filter(email=canonical).first()
    key = hashlib.sha256((canonical or identifier).encode()).hexdigest()[:16]
    counter = f"pwreset:{key}"
    sent = cache.get(counter, 0)
    allowed = sent < RESET_REQUESTS_PER_HOUR
    if allowed:
        cache.set(counter, sent + 1, timeout=3600)
    audit.record(
        "auth.password_reset.requested", request=request, identifier=key, known=user is not None, throttled=not allowed
    )
    if user is not None and user.is_active and allowed:
        dispatch_task(tasks.send_password_reset_email, user.pk)


def confirm_password_reset(request, *, uid: str, token: str, new_password: str) -> User:
    user = None
    try:
        user = User.objects.filter(public_id=uuid.UUID(hex=uid)).first()
    except ValueError:
        pass
    if user is None or user.status == "deactivated" or not default_token_generator.check_token(user, token):
        audit.record("auth.password_reset.invalid_token", request=request, target=user)
        raise ValidationError({"token": ["This reset link is invalid or has expired."]}, code="invalid_token")
    errors = password_error_messages(new_password, user)
    if errors:
        raise ValidationError({"new_password": errors})
    with transaction.atomic():
        user.set_password(new_password)
        if user.email and user.email_verified_at is None:
            user.email_verified_at = timezone.now()  # the token proved control of the mailbox
            user.save(update_fields=["email_verified_at"])
        _finish_password_change(user)
        revoked = sessions.revoke_user_sessions(user, SessionEnd.PASSWORD_RESET)
        audit.record(
            "auth.password_reset.completed", request=request, actor=user, target=user, revoked_sessions=revoked
        )
        dispatch_task(tasks.send_password_changed_notice, user.pk)
    return user


def _require_password(user: User, password: str, request, action: str) -> None:
    if not user.check_password(password):
        audit.record(f"{action}.failed", request=request, target=user, reason="bad_password")
        raise ValidationError({"current_password": ["Incorrect password."]}, code="invalid_password")


def change_email(request, user: User, *, email: str, current_password: str) -> User:
    _require_password(user, current_password, request, "account.email.change")
    if email == user.email:
        return user
    if User.objects.filter(email=email).exclude(pk=user.pk).exists():
        raise Conflict(
            "An account with this email already exists.", code="already_exists", fields={"email": ["Already in use."]}
        )
    old = user.email
    try:
        with transaction.atomic():
            user.email = email
            user.email_verified_at = None
            user.save(update_fields=["email", "email_verified_at"])
            audit.record("account.email.changed", request=request, target=user, had_previous=bool(old))
            dispatch_task(tasks.send_email_verification, user.pk)
    except IntegrityError as exc:
        raise Conflict("An account with this email already exists.", code="already_exists") from exc
    return user


def change_phone(request, user: User, *, phone: str, current_password: str) -> User:
    _require_password(user, current_password, request, "account.phone.change")
    if phone == user.phone:
        return user
    if User.objects.filter(phone=phone).exclude(pk=user.pk).exists():
        raise Conflict(
            "An account with this phone number already exists.",
            code="already_exists",
            fields={"phone": ["Already in use."]},
        )
    try:
        with transaction.atomic():
            user.phone = phone
            user.phone_verified_at = None
            user.save(update_fields=["phone", "phone_verified_at"])
            audit.record("account.phone.changed", request=request, target=user)
    except IntegrityError as exc:
        raise Conflict("An account with this phone number already exists.", code="already_exists") from exc
    return user
