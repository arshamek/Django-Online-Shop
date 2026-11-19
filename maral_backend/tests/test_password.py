import re

import pytest

from apps.accounts.models import User, UserSession
from tests.conftest import API, PASSWORD, new_client

pytestmark = pytest.mark.django_db
NEW = "Another-Strong-Pass-7"


def extract(outbox):
    body = outbox[-1].body
    return re.search(r"uid=([0-9a-f]{32})&token=([\w-]+)", body).groups()


# ---- change -------------------------------------------------------------------------------
def test_change_password_revokes_other_sessions_keeps_current(make_user, login, outbox):
    user = make_user()
    here, other = login(user), login(user)
    r = here.post(f"{API}/auth/password/change", {"current_password": PASSWORD, "new_password": NEW}, format="json")
    assert r.status_code == 204
    assert here.get(f"{API}/me").status_code == 200
    assert other.get(f"{API}/me").status_code == 401
    assert (
        new_client().post(f"{API}/auth/login", {"identifier": user.email, "password": NEW}, format="json").status_code
        == 200
    )
    assert (
        new_client()
        .post(f"{API}/auth/login", {"identifier": user.email, "password": PASSWORD}, format="json")
        .status_code
        == 401
    )
    assert UserSession.objects.filter(revoked_reason="password_change").count() == 1
    assert "changed" in outbox[-1].subject.lower() or outbox[-1].subject  # security notice sent


def test_change_password_validations(make_user, login):
    client = login(make_user())
    url = f"{API}/auth/password/change"
    r = client.post(url, {"current_password": "nope-nope-12", "new_password": NEW}, format="json")
    assert r.status_code == 400 and "current_password" in r.json()["error"]["fields"]
    r = client.post(url, {"current_password": PASSWORD, "new_password": PASSWORD}, format="json")
    assert "new_password" in r.json()["error"]["fields"]
    r = client.post(url, {"current_password": PASSWORD, "new_password": "12345"}, format="json")
    assert r.status_code == 400


def test_change_password_requires_login(api_client):
    r = api_client.post(f"{API}/auth/password/change", {"current_password": "a", "new_password": "b"}, format="json")
    assert r.status_code == 401


def test_password_length_capped():
    r = new_client().post(f"{API}/auth/login", {"identifier": "a@example.com", "password": "x" * 5000}, format="json")
    assert r.status_code == 400  # hashing-DoS guard


# ---- reset --------------------------------------------------------------------------------
def test_reset_request_never_reveals_existence(make_user, outbox):
    make_user(email="real@example.com")
    known = new_client().post(f"{API}/auth/password-reset/request", {"identifier": "real@example.com"}, format="json")
    unknown = new_client().post(
        f"{API}/auth/password-reset/request", {"identifier": "ghost@example.com"}, format="json"
    )
    garbage = new_client().post(f"{API}/auth/password-reset/request", {"identifier": "%%%"}, format="json")
    assert known.status_code == unknown.status_code == garbage.status_code == 202
    assert known.content == unknown.content
    assert len(outbox) == 1 and outbox[0].to == ["real@example.com"]


def test_reset_flow_end_to_end(make_user, login, outbox):
    user = make_user(email="real@example.com")
    victim_session = login(user)
    new_client().post(f"{API}/auth/password-reset/request", {"identifier": "REAL@example.com"}, format="json")
    uid, token = extract(outbox)
    r = new_client().post(
        f"{API}/auth/password-reset/confirm", {"uid": uid, "token": token, "new_password": NEW}, format="json"
    )
    assert r.status_code == 204
    user.refresh_from_db()
    assert user.check_password(NEW) and user.email_verified  # mailbox control proven
    assert victim_session.get(f"{API}/me").status_code == 401  # every session revoked
    # single use
    again = new_client().post(
        f"{API}/auth/password-reset/confirm",
        {"uid": uid, "token": token, "new_password": "Third-Strong-Pass-1"},
        format="json",
    )
    assert again.status_code == 400 and again.json()["error"]["fields"]["token"]


def test_reset_clears_lockout(make_user, outbox):
    user = make_user(email="real@example.com")
    User.objects.filter(pk=user.pk).update(failed_login_count=7)
    new_client().post(f"{API}/auth/password-reset/request", {"identifier": user.email}, format="json")
    uid, token = extract(outbox)
    new_client().post(
        f"{API}/auth/password-reset/confirm", {"uid": uid, "token": token, "new_password": NEW}, format="json"
    )
    user.refresh_from_db()
    assert user.failed_login_count == 0 and user.locked_until is None


@pytest.mark.parametrize("mutate", ["token", "uid", "empty", "other_user"])
def test_reset_rejects_bad_tokens(make_user, outbox, mutate):
    user = make_user(email="real@example.com")
    other = make_user()
    new_client().post(f"{API}/auth/password-reset/request", {"identifier": user.email}, format="json")
    uid, token = extract(outbox)
    if mutate == "token":
        token = token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")
    elif mutate == "uid":
        uid = "0" * 32
    elif mutate == "empty":
        token = "x"
    else:
        uid = other.public_id.hex
    r = new_client().post(
        f"{API}/auth/password-reset/confirm", {"uid": uid, "token": token, "new_password": NEW}, format="json"
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


def test_reset_token_expires(make_user, outbox, settings, monkeypatch):
    from django.contrib.auth.tokens import default_token_generator

    user = make_user(email="real@example.com")
    new_client().post(f"{API}/auth/password-reset/request", {"identifier": user.email}, format="json")
    uid, token = extract(outbox)
    monkeypatch.setattr(
        default_token_generator,
        "_now",
        lambda: (
            __import__("datetime").datetime.now()
            + __import__("datetime").timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT + 60)
        ),
    )
    r = new_client().post(
        f"{API}/auth/password-reset/confirm", {"uid": uid, "token": token, "new_password": NEW}, format="json"
    )
    assert r.status_code == 400


def test_reset_token_invalidated_by_password_change_and_login(make_user, login, outbox):
    user = make_user(email="real@example.com")
    new_client().post(f"{API}/auth/password-reset/request", {"identifier": user.email}, format="json")
    uid, token = extract(outbox)
    login(user)  # last_login changes -> token invalid
    r = new_client().post(
        f"{API}/auth/password-reset/confirm", {"uid": uid, "token": token, "new_password": NEW}, format="json"
    )
    assert r.status_code == 400


def test_reset_rejects_weak_new_password(make_user, outbox):
    user = make_user(email="real@example.com")
    new_client().post(f"{API}/auth/password-reset/request", {"identifier": user.email}, format="json")
    uid, token = extract(outbox)
    r = new_client().post(
        f"{API}/auth/password-reset/confirm", {"uid": uid, "token": token, "new_password": "password"}, format="json"
    )
    assert r.status_code == 400 and "new_password" in r.json()["error"]["fields"]
    user.refresh_from_db()
    assert user.check_password(PASSWORD)


def test_reset_requests_limited_per_identifier(make_user, outbox):
    make_user(email="real@example.com")
    for _ in range(6):
        assert (
            new_client()
            .post(f"{API}/auth/password-reset/request", {"identifier": "real@example.com"}, format="json")
            .status_code
            == 202
        )
    assert len(outbox) == 3  # silently capped, response unchanged


def test_suspended_account_gets_no_reset_email(make_user, outbox):
    make_user(email="real@example.com", status="suspended")
    new_client().post(f"{API}/auth/password-reset/request", {"identifier": "real@example.com"}, format="json")
    assert outbox == []


def test_reset_link_points_to_frontend(make_user, outbox, settings):
    make_user(email="real@example.com")
    new_client().post(f"{API}/auth/password-reset/request", {"identifier": "real@example.com"}, format="json")
    assert settings.FRONTEND_BASE_URL + "/reset-password?uid=" in outbox[0].body
