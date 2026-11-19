import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.delivery import LocmemSMSBackend
from apps.accounts.models import PhoneVerificationCode
from tests.conftest import API, PASSWORD

pytestmark = pytest.mark.django_db


def last_code():
    return re.search(r"(\d{6})", LocmemSMSBackend.outbox[-1][1]).group(1)


def email_token(outbox):
    return re.search(r"token=([^\s]+)", outbox[-1].body).group(1)


# ---- email --------------------------------------------------------------------------------
def test_email_verification_roundtrip(make_user, login, outbox, api_client):
    user = make_user(email="v@example.com")
    client = login(user)
    assert client.post(f"{API}/auth/email/verification/request").status_code == 202
    token = email_token(outbox)
    # confirm works without a session (user clicks the link in a fresh browser)
    r = api_client.post(f"{API}/auth/email/verification/confirm", {"token": token}, format="json")
    assert r.status_code == 204
    user.refresh_from_db()
    assert user.email_verified and user.email_verified_at
    assert client.get(f"{API}/me").json()["email_verified"] is True
    assert client.post(f"{API}/auth/email/verification/request").status_code == 409  # already verified


def test_email_verification_resend_cooldown(make_user, login, outbox):
    client = login(make_user(email="v@example.com"))
    assert client.post(f"{API}/auth/email/verification/request").status_code == 202
    r = client.post(f"{API}/auth/email/verification/request")
    assert r.status_code == 429 and r["Retry-After"]


def test_email_verification_invalid_tokens(api_client, make_user):
    make_user(email="v@example.com")
    for token in ["garbage", "a:b:c", ""]:
        r = api_client.post(f"{API}/auth/email/verification/confirm", {"token": token}, format="json")
        assert r.status_code == 400, token


def test_email_token_is_bound_to_current_address(make_user, login, outbox, api_client):
    user = make_user(email="old@example.com")
    client = login(user)
    client.post(f"{API}/auth/email/verification/request")
    old_token = email_token(outbox)
    r = client.put(f"{API}/me/email", {"email": "new@example.com", "current_password": PASSWORD}, format="json")
    assert r.status_code == 200
    assert (
        api_client.post(f"{API}/auth/email/verification/confirm", {"token": old_token}, format="json").status_code
        == 400
    )
    assert not r.json()["email_verified"]
    assert (
        api_client.post(
            f"{API}/auth/email/verification/confirm", {"token": email_token(outbox)}, format="json"
        ).status_code
        == 204
    )


def test_email_token_expires(make_user, login, outbox, api_client, settings):
    login(make_user(email="v@example.com")).post(f"{API}/auth/email/verification/request")
    token = email_token(outbox)
    settings.EMAIL_VERIFICATION_MAX_AGE = -1
    assert api_client.post(f"{API}/auth/email/verification/confirm", {"token": token}, format="json").status_code == 400


# ---- phone --------------------------------------------------------------------------------
def test_phone_verification_roundtrip(make_user, login):
    user = make_user(phone="09121234567")
    client = login(user)
    assert client.post(f"{API}/auth/phone/verification/request").status_code == 202
    assert LocmemSMSBackend.outbox[-1][0] == "+989121234567"
    stored = PhoneVerificationCode.objects.get()
    assert last_code() not in stored.code_hash and len(stored.code_hash) == 64  # only a keyed hash is stored
    assert (
        client.post(f"{API}/auth/phone/verification/confirm", {"code": last_code()}, format="json").status_code == 204
    )
    assert client.get(f"{API}/me").json()["phone_verified"] is True
    assert client.post(f"{API}/auth/phone/verification/request").status_code == 409


def test_phone_code_accepts_persian_digits(make_user, login):
    client = login(make_user(phone="09121234567"))
    client.post(f"{API}/auth/phone/verification/request")
    persian = last_code().translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    assert client.post(f"{API}/auth/phone/verification/confirm", {"code": persian}, format="json").status_code == 204


def test_wrong_code_then_exhaustion(make_user, login):
    client = login(make_user(phone="09121234567"))
    client.post(f"{API}/auth/phone/verification/request")
    good = last_code()
    wrong = "000000" if good != "000000" else "111111"
    for _ in range(5):
        r = client.post(f"{API}/auth/phone/verification/confirm", {"code": wrong}, format="json")
        assert r.status_code == 400 and r.json()["error"]["code"] == "otp_invalid"
    r = client.post(f"{API}/auth/phone/verification/confirm", {"code": good}, format="json")
    assert r.status_code == 400  # exhausted: even the right code no longer works
    assert client.get(f"{API}/me").status_code == 200  # a 400, not a 401 - the session stays valid


def test_expired_code(make_user, login):
    client = login(make_user(phone="09121234567"))
    client.post(f"{API}/auth/phone/verification/request")
    PhoneVerificationCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    r = client.post(f"{API}/auth/phone/verification/confirm", {"code": last_code()}, format="json")
    assert r.status_code == 400 and r.json()["error"]["code"] == "otp_expired"


def test_new_request_invalidates_previous_code(make_user, login):
    client = login(make_user(phone="09121234567"))
    client.post(f"{API}/auth/phone/verification/request")
    first = last_code()
    client.post(f"{API}/auth/phone/verification/request")
    second = last_code()
    if first != second:
        assert client.post(f"{API}/auth/phone/verification/confirm", {"code": first}, format="json").status_code == 400
    assert client.post(f"{API}/auth/phone/verification/confirm", {"code": second}, format="json").status_code == 204


def test_otp_request_limit_per_phone(make_user, login):
    client = login(make_user(phone="09121234567"))
    codes = [client.post(f"{API}/auth/phone/verification/request").status_code for _ in range(4)]
    assert codes == [202, 202, 202, 429]
    assert len(LocmemSMSBackend.outbox) == 3


def test_otp_limit_is_per_number_not_per_account(make_user, login):
    a = login(make_user(phone="09121234567"))
    for _ in range(3):
        a.post(f"{API}/auth/phone/verification/request")
    b = login(make_user(phone="09129999999"))
    assert b.post(f"{API}/auth/phone/verification/request").status_code == 202


def test_no_sms_provider_gives_503_and_stores_nothing(make_user, login, settings):
    settings.SMS_BACKEND = "apps.accounts.delivery.DisabledSMSBackend"
    client = login(make_user(phone="09121234567"))
    r = client.post(f"{API}/auth/phone/verification/request")
    assert r.status_code == 503 and r.json()["error"]["code"] == "sms_unavailable"
    assert PhoneVerificationCode.objects.count() == 0


def test_phone_verification_needs_a_phone(make_user, login):
    client = login(make_user(email="e@example.com"))
    r = client.post(f"{API}/auth/phone/verification/request")
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_phone"


def test_changing_phone_resets_verification_and_requires_password(make_user, login):
    user = make_user(phone="09121234567")
    client = login(user)
    client.post(f"{API}/auth/phone/verification/request")
    client.post(f"{API}/auth/phone/verification/confirm", {"code": last_code()}, format="json")
    bad = client.put(f"{API}/me/phone", {"phone": "09120000000", "current_password": "nope-nope-12"}, format="json")
    assert bad.status_code == 400
    ok = client.put(f"{API}/me/phone", {"phone": "۰۹۱۲۰۰۰۰۰۰۰", "current_password": PASSWORD}, format="json")
    assert ok.status_code == 200 and ok.json()["phone"] == "+989120000000" and ok.json()["phone_verified"] is False


def test_identifier_change_conflict(make_user, login):
    make_user(email="taken@example.com")
    client = login(make_user())
    r = client.put(f"{API}/me/email", {"email": "TAKEN@example.com", "current_password": PASSWORD}, format="json")
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_exists"
