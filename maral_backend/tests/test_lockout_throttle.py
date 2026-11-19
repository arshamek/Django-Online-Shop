from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.models import User
from apps.accounts.services.lockout import lock_seconds
from tests.conftest import API, PASSWORD, new_client

pytestmark = pytest.mark.django_db
LOGIN = f"{API}/auth/login"


def attempt(identifier, password="Wrong-Pass-123"):
    return new_client().post(LOGIN, {"identifier": identifier, "password": password}, format="json")


def test_lock_schedule_is_progressive_and_capped(settings):
    assert [lock_seconds(n) for n in (1, 4, 5, 6, 7, 8)] == [0, 0, 60, 120, 240, 480]
    assert lock_seconds(50) == settings.LOGIN_LOCKOUT_MAX_SECONDS


def test_account_locks_after_threshold_even_with_correct_password(make_user):
    user = make_user()
    for _ in range(5):
        assert attempt(user.email).status_code == 401
    locked = attempt(user.email, PASSWORD)  # correct password, but locked
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "rate_limited"
    assert int(locked["Retry-After"]) > 0
    user.refresh_from_db()
    assert user.locked_until and user.failed_login_count == 5


def test_lock_expires_and_success_resets_counter(make_user):
    user = make_user()
    for _ in range(5):
        attempt(user.email)
    User.objects.filter(pk=user.pk).update(locked_until=timezone.now() - timedelta(seconds=1))
    assert attempt(user.email, PASSWORD).status_code == 200
    user.refresh_from_db()
    assert user.failed_login_count == 0 and user.locked_until is None


def test_lock_duration_doubles(make_user):
    user = make_user()
    for _ in range(5):
        attempt(user.email)
    user.refresh_from_db()
    first = (user.locked_until - timezone.now()).total_seconds()
    User.objects.filter(pk=user.pk).update(locked_until=None)
    attempt(user.email)
    user.refresh_from_db()
    second = (user.locked_until - timezone.now()).total_seconds()
    assert first <= 60 and 60 < second <= 120


def test_unknown_identifier_gets_identical_lockout_behaviour():
    for _ in range(5):
        assert attempt("ghost@example.com").status_code == 401
    response = attempt("ghost@example.com")
    assert response.status_code == 429 and response["Retry-After"]


def test_lockout_is_per_identifier(make_user):
    a, b = make_user(), make_user()
    for _ in range(5):
        attempt(a.email)
    assert attempt(a.email, PASSWORD).status_code == 429
    assert attempt(b.email, PASSWORD).status_code == 200


def test_lockout_covers_email_and_phone_of_same_account_separately_but_normalised(make_user):
    user = make_user(phone="09121234567")
    for raw in ["09121234567", "۰۹۱۲۱۲۳۴۵۶۷", "+989121234567", "0912 123 4567", "+98-912-123-4567"]:
        attempt(raw)
    assert attempt("09121234567", PASSWORD).status_code == 429  # variants share one counter
    user.refresh_from_db()
    assert user.failed_login_count == 5


def test_per_ip_login_throttle(monkeypatch, make_user):
    user = make_user()
    monkeypatch.setitem(ScopedRateThrottle.THROTTLE_RATES, "auth_login", "3/hour")
    codes = [attempt(user.email, PASSWORD).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_registration_and_reset_are_throttled(monkeypatch):
    monkeypatch.setitem(ScopedRateThrottle.THROTTLE_RATES, "auth_register", "2/hour")
    monkeypatch.setitem(ScopedRateThrottle.THROTTLE_RATES, "auth_password_reset", "2/hour")
    codes = [
        new_client()
        .post(f"{API}/auth/register", {"email": f"n{i}@example.com", "password": PASSWORD}, format="json")
        .status_code
        for i in range(3)
    ]
    assert codes == [201, 201, 429]
    codes = [
        new_client()
        .post(f"{API}/auth/password-reset/request", {"identifier": "a@example.com"}, format="json")
        .status_code
        for _ in range(3)
    ]
    assert codes == [202, 202, 429]


def test_x_forwarded_for_is_ignored_by_default(monkeypatch, make_user):
    """With TRUSTED_PROXY_COUNT=0 a spoofed header must not give an attacker a fresh throttle bucket."""
    user = make_user()
    monkeypatch.setitem(ScopedRateThrottle.THROTTLE_RATES, "auth_login", "2/hour")
    codes = []
    for i in range(3):
        r = new_client().post(
            LOGIN, {"identifier": user.email, "password": PASSWORD}, format="json", HTTP_X_FORWARDED_FOR=f"9.9.9.{i}"
        )
        codes.append(r.status_code)
    assert codes == [200, 200, 429]


def test_client_ip_helper_honours_trusted_proxies(settings):
    from django.test import RequestFactory

    from apps.common.net import get_client_ip

    request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="6.6.6.6, 1.2.3.4", REMOTE_ADDR="10.0.0.1")
    assert get_client_ip(request) == "10.0.0.1"
    settings.TRUSTED_PROXY_COUNT = 1
    assert get_client_ip(request) == "1.2.3.4"  # spoofed left-most entry ignored
