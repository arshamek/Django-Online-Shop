"""Authorization: customer isolation and the staff role matrix."""

import pytest

from apps.accounts.models import AccountStatus, User
from apps.accounts.roles import ROLE_PERMISSIONS
from tests.conftest import API, PASSWORD, new_client

pytestmark = pytest.mark.django_db

ADDRESS = {
    "recipient_name": "A",
    "phone": "09121234567",
    "country": "IR",
    "city": "تهران",
    "address_line1": "x",
    "postal_code": "1234567890",
}


# ---- customers can never touch each other's data ------------------------------------------
def test_customers_cannot_access_other_customers_addresses(make_user, login):
    alice, bob = login(make_user()), login(make_user())
    addr = alice.post(f"{API}/me/addresses", ADDRESS, format="json").json()["id"]
    url = f"{API}/me/addresses/{addr}"
    assert bob.get(url).status_code == 404
    assert bob.patch(url, {"city": "hacked"}, format="json").status_code == 404
    assert bob.delete(url).status_code == 404
    assert bob.get(f"{API}/me/addresses").json()["count"] == 0
    assert alice.get(url).json()["city"] == "تهران"  # untouched


def test_other_users_response_is_indistinguishable_from_missing(make_user, login):
    alice, bob = login(make_user()), login(make_user())
    addr = alice.post(f"{API}/me/addresses", ADDRESS, format="json").json()["id"]
    real = bob.get(f"{API}/me/addresses/{addr}")
    fake = bob.get(f"{API}/me/addresses/00000000-0000-0000-0000-000000000000")
    assert real.status_code == fake.status_code == 404
    assert real.json()["error"]["code"] == fake.json()["error"]["code"]


def test_me_endpoints_ignore_client_supplied_ids(make_user, login):
    alice, bob_user = login(make_user()), make_user(email="bob@example.com")
    assert alice.get(f"{API}/me?user_id={bob_user.public_id}").json()["email"] != "bob@example.com"
    r = alice.post(f"{API}/me/addresses", {**ADDRESS, "user": bob_user.pk, "user_id": bob_user.pk}, format="json")
    assert r.status_code == 201
    assert bob_user.addresses.count() == 0


def test_customer_cannot_see_or_revoke_other_sessions(make_user, login):
    alice_user = make_user()
    alice, bob = login(alice_user), login(make_user())
    sid = alice.get(f"{API}/auth/sessions").json()["results"][0]["id"]
    assert bob.delete(f"{API}/auth/sessions/{sid}").status_code == 404
    assert alice.get(f"{API}/me").status_code == 200


STAFF_ENDPOINTS = [
    ("get", "/staff/customers"),
    ("get", "/staff/customers/{cid}"),
    ("post", "/staff/customers/{cid}/suspend"),
    ("post", "/staff/customers/{cid}/reactivate"),
    ("post", "/staff/customers/{cid}/unlock"),
    ("post", "/staff/customers/{cid}/revoke-sessions"),
    ("get", "/staff/audit-logs"),
]


@pytest.mark.parametrize("method,path", STAFF_ENDPOINTS)
def test_customers_are_forbidden_from_staff_api(make_user, login, method, path):
    customer = make_user()
    client = login(make_user())
    r = getattr(client, method)(f"{API}{path.format(cid=customer.public_id)}", {"reason": "x"}, format="json")
    assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied"


@pytest.mark.parametrize("method,path", STAFF_ENDPOINTS)
def test_anonymous_is_unauthorized_on_staff_api(api_client, make_user, method, path):
    customer = make_user()
    r = getattr(api_client, method)(f"{API}{path.format(cid=customer.public_id)}", {}, format="json")
    assert r.status_code == 401


def test_is_staff_flag_alone_grants_nothing(make_user, login):
    customer = make_user()
    client = login(make_user(is_staff=True))
    assert client.get(f"{API}/staff/customers").status_code == 403
    assert client.get(f"{API}/staff/customers/{customer.public_id}").status_code == 403


# ---- role matrix --------------------------------------------------------------------------
ACTIONS = {
    "customers_view": ("get", "/staff/customers"),
    "customers_view_detail": ("get", "/staff/customers/{cid}"),
    "customers_suspend": ("post", "/staff/customers/{cid}/suspend"),
    "customers_unlock": ("post", "/staff/customers/{cid}/unlock"),
    "auditlog_view": ("get", "/staff/audit-logs"),
}
EXPECTED = {
    "Product Manager": set(),
    "Import Manager": set(),
    "Finance Manager": set(),
    "Order Manager": {"customers_view", "customers_view_detail"},
    "Customer Support": {"customers_view", "customers_view_detail", "customers_suspend", "customers_unlock"},
    "Super Admin": set(ACTIONS),
}


@pytest.mark.parametrize("role", list(EXPECTED))
def test_role_matrix(make_user, make_staff, login, role):
    customer = make_user()
    staff = make_staff(role=None if role == "Super Admin" else role, superuser=role == "Super Admin")
    client = login(staff)
    for action, (method, path) in ACTIONS.items():
        r = getattr(client, method)(f"{API}{path.format(cid=customer.public_id)}", {"reason": "abuse"}, format="json")
        assert (r.status_code != 403) == (action in EXPECTED[role]), f"{role} / {action} -> {r.status_code}"


def test_role_definitions_cover_the_six_roles():
    assert set(ROLE_PERMISSIONS) == {
        "Product Manager",
        "Import Manager",
        "Finance Manager",
        "Order Manager",
        "Customer Support",
    }


def test_me_exposes_roles_and_permissions_for_staff(make_staff, login):
    body = login(make_staff(role="Customer Support")).get(f"{API}/me").json()
    assert body["roles"] == ["Customer Support"] and "customers_suspend" in body["permissions"]
    assert login(make_staff(superuser=True)).get(f"{API}/me").json()["roles"] == ["Super Admin"]


# ---- staff actions ------------------------------------------------------------------------
def test_support_can_suspend_reactivate_unlock(make_user, make_staff, login):
    customer = make_user()
    victim = login(customer)
    support = login(make_staff(role="Customer Support"))
    r = support.post(f"{API}/staff/customers/{customer.public_id}/suspend", {"reason": "fraud"}, format="json")
    assert r.status_code == 200 and r.json()["status"] == AccountStatus.SUSPENDED
    assert victim.get(f"{API}/me").status_code == 401  # sessions revoked
    assert (
        new_client()
        .post(f"{API}/auth/login", {"identifier": customer.email, "password": PASSWORD}, format="json")
        .status_code
        == 403
    )
    assert support.post(f"{API}/staff/customers/{customer.public_id}/reactivate").json()["status"] == "active"
    User.objects.filter(pk=customer.pk).update(failed_login_count=9)
    assert support.post(f"{API}/staff/customers/{customer.public_id}/unlock").json()["failed_login_count"] == 0


def test_suspend_requires_reason(make_user, make_staff, login):
    r = login(make_staff(role="Customer Support")).post(
        f"{API}/staff/customers/{make_user().public_id}/suspend", {}, format="json"
    )
    assert r.status_code == 400


def test_support_cannot_act_on_staff_or_self(make_user, make_staff, login):
    support_user = make_staff(role="Customer Support")
    support = login(support_user)
    boss = make_staff(superuser=True)
    assert (
        support.post(f"{API}/staff/customers/{boss.public_id}/suspend", {"reason": "x"}, format="json").status_code
        == 404
    )  # staff are not "customers"
    assert (
        support.post(
            f"{API}/staff/customers/{support_user.public_id}/suspend", {"reason": "x"}, format="json"
        ).status_code
        == 404
    )


def test_staff_customer_list_filters_and_excludes_staff(make_user, make_staff, login):
    make_user(email="find.me@example.com")
    make_user(status="suspended")
    support = login(make_staff(role="Customer Support"))
    body = support.get(f"{API}/staff/customers?q=find.me").json()
    assert body["count"] == 1
    assert support.get(f"{API}/staff/customers?status=suspended").json()["count"] == 1
    assert all(not r.get("is_staff") for r in support.get(f"{API}/staff/customers").json()["results"])
    assert "password" not in str(body)


def test_staff_detail_404_for_unknown(make_staff, login):
    r = login(make_staff(role="Customer Support")).get(f"{API}/staff/customers/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
