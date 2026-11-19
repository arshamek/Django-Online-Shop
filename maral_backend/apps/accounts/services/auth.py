"""Registration, login, logout and session refresh."""

from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.middleware.csrf import get_token, rotate_token
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

from apps.audit import services as audit
from apps.common.exceptions import Conflict
from apps.common.transactions import dispatch_task

from .. import tasks
from ..models import AccountStatus, CustomerProfile, SessionEnd, User, UserSession
from ..normalizers import looks_like_email, normalize_email, normalize_phone
from . import lockout, sessions

BACKEND = "apps.accounts.backends.IdentifierBackend"
_dummy_hash: str | None = None


def password_error_messages(password: str, user: User | None = None) -> list[str]:
    try:
        validate_password(password, user)
    except DjangoValidationError as exc:
        return list(exc.messages)
    return []


def register(request, *, password, email=None, phone=None, first_name="", last_name="", language="fa") -> User:
    """Create the account and its profile. Raises Conflict(already_exists) when an identifier is taken."""
    conflicts = {}
    if email and User.objects.filter(email=email).exists():
        conflicts["email"] = ["An account with this email already exists."]
    if phone and User.objects.filter(phone=phone).exists():
        conflicts["phone"] = ["An account with this phone number already exists."]
    if conflicts:
        audit.record("auth.register.conflict", request=request, fields=sorted(conflicts))
        raise Conflict("Account already exists.", code="already_exists", fields=conflicts)
    try:
        with transaction.atomic():
            user = User.objects.create_user(
                email=email,
                phone=phone,
                password=password,
                first_name=first_name,
                last_name=last_name,
                password_changed_at=timezone.now(),
            )
            CustomerProfile.objects.create(user=user, language=language)
            audit.record(
                "auth.register", request=request, actor=user, target=user, has_email=bool(email), has_phone=bool(phone)
            )
            if email:
                dispatch_task(tasks.send_email_verification, user.pk)
    except IntegrityError as exc:  # concurrent registration won the race
        raise Conflict("Account already exists.", code="already_exists") from exc
    return user


def find_user(identifier: str) -> tuple[User | None, str]:
    """Resolve an email-or-phone identifier to (user|None, canonical identifier used as throttle key)."""
    try:
        if looks_like_email(identifier):
            canonical = normalize_email(identifier) or ""
            return User.objects.filter(email=canonical).first(), canonical
        canonical = normalize_phone(identifier) or ""
        return User.objects.filter(phone=canonical).first(), canonical
    except (ValueError, DjangoValidationError):
        return None, identifier.strip().lower()


def authenticate_credentials(request, identifier: str, password: str) -> User:
    global _dummy_hash
    user, canonical = find_user(identifier)
    key = lockout.identifier_key(canonical)
    lockout.ensure_not_locked(user, key)

    if user is None:
        # Equalise timing with the "wrong password" path so unknown identifiers are not distinguishable.
        _dummy_hash = _dummy_hash or make_password("timing-equaliser")
        check_password(password, _dummy_hash)
        valid = False
    else:
        valid = user.check_password(password)

    if not valid:
        lockout.register_failure(request, user, key)
        audit.record(
            "auth.login.failed", request=request, actor=None, target=user, identifier=key, known=user is not None
        )
        raise AuthenticationFailed("Incorrect email/phone or password.", code="invalid_credentials")

    if user.status != AccountStatus.ACTIVE:
        audit.record("auth.login.blocked", request=request, target=user, status=user.status)
        code = "account_suspended" if user.status == AccountStatus.SUSPENDED else "account_deactivated"
        raise PermissionDenied("This account cannot sign in. Contact support.", code)
    lockout.register_success(user, key)
    return user


def login(request, user: User) -> str:
    """Start a session (rotates the session key and CSRF token). Returns the new CSRF token."""
    django_login(request, user, backend=BACKEND)
    request.session.set_expiry(settings.SESSION_COOKIE_AGE)
    audit.record("auth.login.success", request=request, actor=user, target=user)
    return get_token(request)


def logout(request, user_session: UserSession | None) -> None:
    user = request.user
    if user_session is not None:
        sessions.revoke(user_session, SessionEnd.LOGOUT)
    audit.record("auth.logout", request=request, actor=user, target=user)
    django_logout(request)


def refresh(request, user_session: UserSession) -> tuple[str, datetime]:
    """
    Sliding renewal: rotate the session key and CSRF token and extend the idle expiry, never beyond
    the absolute lifetime. Returns (csrf_token, expires_at).
    """
    http_request = getattr(request, "_request", request)
    now = timezone.now()
    absolute_end = user_session.created_at + timedelta(seconds=settings.SESSION_ABSOLUTE_AGE)
    remaining = int((absolute_end - now).total_seconds())
    if remaining <= 0:
        raise AuthenticationFailed("Your session has expired. Please sign in again.", code="session_expired")
    lifetime = min(settings.SESSION_COOKIE_AGE, remaining)
    http_request.session.cycle_key()
    http_request.session.set_expiry(lifetime)
    rotate_token(http_request)
    user_session.last_seen_at = now
    user_session.save(update_fields=["last_seen_at"])
    return get_token(http_request), now + timedelta(seconds=lifetime)
