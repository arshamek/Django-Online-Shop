import pytest
from django.contrib.auth.models import Group
from django.db import IntegrityError, transaction

from apps.accounts.models import AccountStatus, User
from apps.accounts.normalizers import normalize_email, normalize_phone, normalize_text
from apps.accounts.roles import ALL_ROLES, ROLE_PERMISSIONS, roles_for

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("09121234567", "+989121234567"),
        ("۰۹۱۲۱۲۳۴۵۶۷", "+989121234567"),  # Persian digits
        ("٠٩١٢١٢٣٤٥٦٧", "+989121234567"),  # Arabic-Indic digits
        ("+98 912 123 4567", "+989121234567"),
        ("00989121234567", "+989121234567"),
        ("+905321234567", "+905321234567"),
    ],
)
def test_phone_normalisation(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["123", "abc", "09121", "+98912"])
def test_invalid_phone_rejected(raw):
    with pytest.raises(ValueError):
        normalize_phone(raw)


def test_email_and_text_normalisation():
    assert normalize_email("  Sara@Example.COM ") == "sara@example.com"
    assert normalize_email("   ") is None
    assert normalize_text("  علي   كريمي ") == "علی کریمی"  # Arabic yeh/kaf -> Persian, spaces collapsed


def test_identifiers_normalised_on_save_and_unique():
    User.objects.create_user(email="A@Example.com", phone="09121234567", password="x" * 12)
    stored = User.objects.get()
    assert (stored.email, stored.phone) == ("a@example.com", "+989121234567")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user(email="a@EXAMPLE.com", password="x" * 12)
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user(phone="+98 912 123 4567", password="x" * 12)


def test_many_accounts_may_lack_one_identifier():
    for i in range(3):
        User.objects.create_user(email=f"u{i}@example.com", password="x" * 12)  # no phone
    for i in range(3):
        User.objects.create_user(phone=f"+98912000000{i}", password="x" * 12)  # no email
    assert User.objects.count() == 6


def test_at_least_one_identifier_required():
    with pytest.raises(ValueError):
        User.objects.create_user(password="x" * 12)
    with pytest.raises(IntegrityError), transaction.atomic():  # DB-level CHECK constraint
        User.objects.bulk_create([User(first_name="no identifiers")])


def test_status_and_verification_flags():
    user = User.objects.create_user(email="s@example.com", password="x" * 12)
    assert user.status == AccountStatus.ACTIVE and user.is_active
    assert not user.email_verified and not user.phone_verified
    user.status = AccountStatus.SUSPENDED
    assert not user.is_active
    assert user.created_at and user.last_login is None


def test_createsuperuser_requires_email_and_sets_flags():
    admin = User.objects.create_superuser("root@example.com", "x" * 12)
    assert admin.is_staff and admin.is_superuser and admin.email_verified
    assert roles_for(admin) == ["Super Admin"]


def test_production_hasher_is_argon2():
    import config.settings.base as base

    assert base.PASSWORD_HASHERS[0].endswith("Argon2PasswordHasher")


def test_roles_synced_with_permissions():
    assert set(Group.objects.values_list("name", flat=True)) >= set(ROLE_PERMISSIONS)
    support = Group.objects.get(name="Customer Support")
    assert set(support.permissions.values_list("codename", flat=True)) == set(ROLE_PERMISSIONS["Customer Support"])
    assert len(ALL_ROLES) == 6
