import pytest

from apps.accounts.models import UserSession
from apps.audit.models import AppendOnlyError, AuditLog
from tests.conftest import API, PASSWORD, new_client

pytestmark = pytest.mark.django_db


# ---- session management -------------------------------------------------------------------
def test_list_sessions_marks_current_and_masks_ip(make_user, login):
    user = make_user()
    a, _b = login(user), login(user)
    body = a.get(f"{API}/auth/sessions", HTTP_USER_AGENT="Firefox/Test").json()
    assert body["count"] == 2 and [s["is_current"] for s in body["results"]].count(True) == 1
    assert all(s["ip_address"] is None or s["ip_address"].endswith(".*") for s in body["results"])
    assert set(body["results"][0]) == {"id", "created_at", "last_seen_at", "ip_address", "user_agent", "is_current"}


def test_revoke_one_session(make_user, login):
    user = make_user()
    a, b = login(user), login(user)
    other = [s for s in a.get(f"{API}/auth/sessions").json()["results"] if not s["is_current"]][0]
    assert a.delete(f"{API}/auth/sessions/{other['id']}").status_code == 204
    assert b.get(f"{API}/me").status_code == 401
    assert a.get(f"{API}/me").status_code == 200
    assert a.delete(f"{API}/auth/sessions/{other['id']}").status_code == 404  # already gone


def test_revoke_all_others(make_user, login):
    user = make_user()
    a, b, c = login(user), login(user), login(user)
    assert a.post(f"{API}/auth/sessions/revoke-others").status_code == 204
    assert a.get(f"{API}/me").status_code == 200
    assert b.get(f"{API}/me").status_code == 401 and c.get(f"{API}/me").status_code == 401
    assert a.get(f"{API}/auth/sessions").json()["count"] == 1


def test_revoking_current_session_signs_out(make_user, login):
    a = login(make_user())
    sid = a.get(f"{API}/auth/sessions").json()["results"][0]["id"]
    assert a.delete(f"{API}/auth/sessions/{sid}").status_code == 204
    assert a.get(f"{API}/me").status_code == 401


# ---- audit log ----------------------------------------------------------------------------
def actions():
    return list(AuditLog.objects.order_by("id").values_list("action", flat=True))


def test_critical_events_are_audited(make_user, login, outbox):
    new_client().post(f"{API}/auth/register", {"email": "a@example.com", "password": PASSWORD}, format="json")
    new_client().post(f"{API}/auth/login", {"identifier": "a@example.com", "password": "Wrong-Pass-123"}, format="json")
    client = login(User := __import__("apps.accounts.models", fromlist=["User"]).User.objects.get())
    client.post(
        f"{API}/auth/password/change",
        {"current_password": PASSWORD, "new_password": "Another-Strong-Pass-7"},
        format="json",
    )
    client.post(f"{API}/auth/logout")
    got = actions()
    for expected in [
        "auth.register",
        "auth.login.failed",
        "auth.login.success",
        "auth.password.changed",
        "auth.logout",
    ]:
        assert expected in got, expected
    assert User  # silence linters


def test_audit_entries_have_context(make_user):
    user = make_user()
    new_client().post(
        f"{API}/auth/login",
        {"identifier": user.email, "password": "Wrong-Pass-123"},
        format="json",
        HTTP_USER_AGENT="Agent/1",
        HTTP_X_REQUEST_ID="req-audit-1",
    )
    entry = AuditLog.objects.get(action="auth.login.failed")
    assert (
        entry.request_id == "req-audit-1" and entry.user_agent == "Agent/1" and entry.target_id == str(user.public_id)
    )
    assert entry.ip_address


def test_audit_never_stores_secrets_or_raw_identifiers(make_user, outbox):
    user = make_user(email="private.person@example.com", phone="09121234567")
    c = new_client()
    c.post(
        f"{API}/auth/login", {"identifier": "private.person@example.com", "password": "Wrong-Pass-123"}, format="json"
    )
    c.post(f"{API}/auth/password-reset/request", {"identifier": "private.person@example.com"}, format="json")
    blob = " ".join(str(m) for m in AuditLog.objects.values_list("metadata", flat=True))
    for secret in ["Wrong-Pass-123", "private.person", "09121234567", PASSWORD]:
        assert secret not in blob
    assert user.public_id  # target ids are fine


def test_audit_log_is_append_only():
    entry = AuditLog.objects.create(action="test.event")
    entry.action = "tampered"
    with pytest.raises(AppendOnlyError):
        entry.save()
    with pytest.raises(AppendOnlyError):
        entry.delete()
    with pytest.raises(AppendOnlyError):
        AuditLog.objects.all().delete()
    with pytest.raises(AppendOnlyError):
        AuditLog.objects.all().update(action="x")


def test_audit_metadata_is_redacted():
    from apps.audit import services as audit

    entry = audit.record("test.redact", password="hunter2", token="abc", note="ok")
    assert entry.metadata == {"password": "[REDACTED]", "token": "[REDACTED]", "note": "ok"}


def test_staff_actions_and_pii_views_are_audited(make_user, make_staff, login):
    customer = make_user()
    support = login(make_staff(role="Customer Support"))
    support.get(f"{API}/staff/customers/{customer.public_id}")
    support.post(f"{API}/staff/customers/{customer.public_id}/suspend", {"reason": "fraud"}, format="json")
    assert {"staff.customer.viewed", "staff.customer.suspended"} <= set(actions())
    entry = AuditLog.objects.get(action="staff.customer.suspended")
    assert (
        entry.actor is not None and entry.target_id == str(customer.public_id) and entry.metadata["reason"] == "fraud"
    )


def test_role_assignment_is_audited(make_user):
    from django.contrib.auth.models import Group

    user = make_user()
    user.groups.add(Group.objects.get(name="Finance Manager"))
    entry = AuditLog.objects.get(action="staff.groups.add")
    assert entry.metadata["groups"] == ["Finance Manager"] and entry.target_id == str(user.public_id)


def test_only_super_admin_reads_audit_log(make_user, make_staff, login):
    make_user()
    assert login(make_staff(role="Customer Support")).get(f"{API}/staff/audit-logs").status_code == 403
    r = login(make_staff(superuser=True)).get(f"{API}/staff/audit-logs?action=auth.")
    assert r.status_code == 200 and r.json()["count"] >= 1


def test_lockout_is_audited(make_user):
    user = make_user()
    for _ in range(5):
        new_client().post(f"{API}/auth/login", {"identifier": user.email, "password": "Wrong-Pass-123"}, format="json")
    assert "auth.account.locked" in actions()


def test_session_records_created_for_every_login_path(make_user, client):
    user = make_user()
    client.force_login(user)  # e.g. Django admin login also gets a revocable session
    assert UserSession.objects.filter(user=user).count() == 1


def test_cleanup_task_removes_only_dead_records(make_user, login):
    from datetime import timedelta

    from django.utils import timezone

    from apps.accounts import tasks

    user = make_user()
    login(user)
    live = UserSession.objects.get()
    old = UserSession.objects.create(user=user, revoked_at=timezone.now() - timedelta(days=40))
    AuditLog.objects.create(action="keep.me")
    result = tasks.cleanup_expired.delay().get()
    assert result["sessions"] == 1
    assert UserSession.objects.filter(pk=live.pk).exists() and not UserSession.objects.filter(pk=old.pk).exists()
    assert AuditLog.objects.filter(action="keep.me").exists()
