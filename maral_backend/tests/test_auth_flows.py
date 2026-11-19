import pytest
from django.contrib.sessions.models import Session
from rest_framework.test import APIClient

from apps.accounts.models import User, UserSession
from tests.conftest import API, PASSWORD, new_client

pytestmark = pytest.mark.django_db
REGISTER = f"{API}/auth/register"
LOGIN = f"{API}/auth/login"


def reg(client, **over):
    body = {"email": "sara@example.com", "password": PASSWORD, "first_name": "سارا", "last_name": "محمدی", **over}
    return client.post(REGISTER, body, format="json")


# ---- registration -------------------------------------------------------------------------
def test_register_with_email_signs_in_and_sends_verification(api_client, outbox):
    response = reg(api_client)
    body = response.json()
    assert response.status_code == 201
    assert body["user"]["email"] == "sara@example.com" and body["user"]["email_verified"] is False
    assert body["user"]["profile"]["language"] == "fa" and body["user"]["profile"]["currency"] == "IRT"
    assert body["csrf_token"]
    assert api_client.get(f"{API}/me").status_code == 200  # cookie session works
    assert len(outbox) == 1 and "verify-email?token=" in outbox[0].body


def test_register_with_persian_phone_only(api_client, outbox):
    response = api_client.post(REGISTER, {"phone": "۰۹۱۲۱۲۳۴۵۶۸", "password": PASSWORD}, format="json")
    assert response.status_code == 201
    assert response.json()["user"]["phone"] == "+989121234568"
    assert outbox == []


def test_register_requires_an_identifier(api_client):
    response = api_client.post(REGISTER, {"password": PASSWORD}, format="json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert {"email", "phone"} <= set(response.json()["error"]["fields"])


def test_register_rejects_weak_passwords(api_client):
    for weak in ["short1!", "password1234", "1234567890123", "sara@example.com"]:
        response = reg(api_client, password=weak)
        assert response.status_code == 400, weak
        assert "password" in response.json()["error"]["fields"]


def test_register_rejects_password_similar_to_identity(api_client):
    response = reg(
        api_client,
        email="sarahmohammadi@example.com",
        password="sarahmohammadi1",
        first_name="Sarah",
        last_name="Mohammadi",
    )
    assert response.status_code == 400


def test_register_conflict_is_normalised_and_coded(api_client, make_user):
    make_user(email="sara@example.com", phone="09121234567")
    response = reg(new_client(), email="SARA@Example.com")
    assert response.status_code == 409 and response.json()["error"]["code"] == "already_exists"
    response = new_client().post(REGISTER, {"phone": "+98 912 123 4567", "password": PASSWORD}, format="json")
    assert response.status_code == 409


def test_register_rejects_invalid_email_and_phone(api_client):
    assert reg(api_client, email="not-an-email").status_code == 400
    assert api_client.post(REGISTER, {"phone": "123", "password": PASSWORD}, format="json").status_code == 400


def test_password_never_returned_or_stored_plain(api_client):
    body = reg(api_client).json()
    assert "password" not in str(body)
    assert User.objects.get().password.startswith(("argon2", "md5", "pbkdf2"))
    assert PASSWORD not in User.objects.get().password


# ---- login / logout -----------------------------------------------------------------------
def test_login_by_email_and_phone(make_user):
    make_user(email="a@example.com", phone="09121234567")
    for identifier in ["a@example.com", "A@EXAMPLE.COM", "09121234567", "۰۹۱۲۱۲۳۴۵۶۷", "+989121234567"]:
        client = new_client()
        response = client.post(LOGIN, {"identifier": identifier, "password": PASSWORD}, format="json")
        assert response.status_code == 200, identifier
        assert response.json()["user"]["email"] == "a@example.com"


def test_login_updates_last_login_and_creates_session(make_user):
    user = make_user()
    new_client().post(LOGIN, {"identifier": user.email, "password": PASSWORD}, format="json")
    user.refresh_from_db()
    assert user.last_login is not None
    assert UserSession.objects.filter(user=user, revoked_at__isnull=True).count() == 1


def test_wrong_password_and_unknown_user_are_indistinguishable(make_user):
    user = make_user()
    wrong = new_client().post(LOGIN, {"identifier": user.email, "password": "Wrong-Pass-123"}, format="json")
    unknown = new_client().post(LOGIN, {"identifier": "ghost@example.com", "password": "Wrong-Pass-123"}, format="json")
    malformed = new_client().post(LOGIN, {"identifier": "###", "password": "x"}, format="json")
    for r in (wrong, unknown, malformed):
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "invalid_credentials"
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert "sessionid" not in wrong.cookies and "maral_sid" not in wrong.cookies


@pytest.mark.parametrize("state,code", [("suspended", "account_suspended"), ("deactivated", "account_deactivated")])
def test_blocked_accounts_cannot_login(make_user, state, code):
    user = make_user(status=state)
    response = new_client().post(LOGIN, {"identifier": user.email, "password": PASSWORD}, format="json")
    assert response.status_code == 403 and response.json()["error"]["code"] == code
    wrong = new_client().post(LOGIN, {"identifier": user.email, "password": "Wrong-Pass-123"}, format="json")
    assert wrong.status_code == 401  # status is only revealed to someone who knows the password


def test_logout_revokes_session_server_side(make_user, login):
    client = login(make_user())
    sid_cookie = client.cookies["maral_sid"].value
    assert client.post(f"{API}/auth/logout").status_code == 204
    assert client.get(f"{API}/me").status_code == 401
    # replaying the old cookie must fail too
    replay = APIClient()
    replay.cookies["maral_sid"] = sid_cookie
    assert replay.get(f"{API}/me").status_code == 401
    assert UserSession.objects.get().revoked_reason == "logout"


def test_auth_responses_are_not_cacheable(make_user, login):
    client = login(make_user())
    response = client.get(f"{API}/me")
    assert response["Cache-Control"] == "private, no-store"


def test_no_token_in_response_body_and_cookie_flags(make_user):
    user = make_user()
    response = new_client().post(LOGIN, {"identifier": user.email, "password": PASSWORD}, format="json")
    assert set(response.json()) == {"user", "csrf_token"}
    cookie = response.cookies["maral_sid"]
    assert cookie["httponly"] and cookie["samesite"] == "Lax"
    assert response.cookies["maral_csrf"]["httponly"]


# ---- CSRF ---------------------------------------------------------------------------------
def test_csrf_required_for_anonymous_login_and_register(make_user):
    user = make_user()
    client = new_client(csrf=True)
    r = client.post(LOGIN, {"identifier": user.email, "password": PASSWORD}, format="json")
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    r = client.post(REGISTER, {"email": "n@example.com", "password": PASSWORD}, format="json")
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"


def test_full_flow_with_csrf_enforced(make_user, login):
    user = make_user()
    client = login(user, csrf=True)
    assert client.get(f"{API}/me").status_code == 200
    token = client.defaults["HTTP_X_CSRFTOKEN"]
    del client.defaults["HTTP_X_CSRFTOKEN"]
    assert client.post(f"{API}/auth/logout").status_code == 403  # no header -> rejected, session intact
    client.defaults["HTTP_X_CSRFTOKEN"] = token
    assert client.post(f"{API}/auth/logout").status_code == 204


def test_wrong_csrf_token_rejected(make_user, login):
    client = login(make_user(), csrf=True)
    client.defaults["HTTP_X_CSRFTOKEN"] = "x" * 64
    r = client.post(f"{API}/auth/logout")
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"


def test_csrf_endpoint_returns_token(api_client):
    response = api_client.get(f"{API}/auth/csrf")
    assert response.status_code == 200 and len(response.json()["csrf_token"]) >= 32


# ---- refresh / expiry ---------------------------------------------------------------------
def test_refresh_rotates_session_key_and_csrf_token(make_user, login):
    client = login(make_user(), csrf=True)
    before_cookie = client.cookies["maral_sid"].value
    before_csrf = client.defaults["HTTP_X_CSRFTOKEN"]
    response = client.post(f"{API}/auth/refresh")
    assert response.status_code == 200
    body = response.json()
    assert body["csrf_token"] != before_csrf and body["expires_at"]
    assert client.cookies["maral_sid"].value != before_cookie
    client.defaults["HTTP_X_CSRFTOKEN"] = body["csrf_token"]
    assert client.get(f"{API}/me").status_code == 200
    stale = APIClient()
    stale.cookies["maral_sid"] = before_cookie
    assert stale.get(f"{API}/me").status_code == 401  # old key is dead


def test_refresh_requires_authentication(api_client):
    assert api_client.post(f"{API}/auth/refresh").status_code == 401


def test_absolute_lifetime_enforced(make_user, login, settings):
    from datetime import timedelta

    from django.utils import timezone

    client = login(make_user())
    UserSession.objects.update(created_at=timezone.now() - timedelta(seconds=settings.SESSION_ABSOLUTE_AGE + 5))
    response = client.get(f"{API}/me")
    assert response.status_code == 401 and response.json()["error"]["code"] == "session_expired"


def test_revoked_session_reports_session_expired(make_user, login):
    client = login(make_user())
    UserSession.objects.update(revoked_at="2026-01-01T00:00:00Z")
    response = client.get(f"{API}/me")
    assert response.status_code == 401 and response.json()["error"]["code"] == "session_expired"


def test_expired_django_session_is_plain_unauthenticated(make_user, login):
    client = login(make_user())
    Session.objects.all().delete()
    r = client.get(f"{API}/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "not_authenticated"


def test_suspended_user_loses_access_immediately(make_user, login):
    user = make_user()
    client = login(user)
    User.objects.filter(pk=user.pk).update(status="suspended")
    assert client.get(f"{API}/me").status_code == 401


def test_unauthenticated_access_is_401(api_client):
    for path in ["/me", "/me/addresses", "/auth/sessions", "/staff/customers"]:
        r = api_client.get(f"{API}{path}")
        assert r.status_code == 401 and r.json()["error"]["code"] == "not_authenticated", path
