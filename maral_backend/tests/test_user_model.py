import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

pytestmark = pytest.mark.django_db
User = get_user_model()


def test_custom_user_model_is_active():
    assert User._meta.label == "accounts.User"


def test_many_users_without_email_or_phone():
    for i in range(3):
        User.objects.create_user(f"u{i}", password="x" * 12)
    assert User.objects.filter(email__isnull=True, phone__isnull=True).count() == 3


def test_blank_strings_stored_as_null():
    user = User.objects.create_user("blank", email="", password="x" * 12)
    user.phone = ""
    user.save()
    user.refresh_from_db()
    assert user.email is None and user.phone is None


def test_duplicate_email_and_phone_rejected():
    User.objects.create_user("a", email="a@example.com", password="x" * 12, phone="+989121234567")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("b", email="a@example.com", password="x" * 12)
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("c", password="x" * 12, phone="+989121234567")


def test_public_id_generated_and_unique():
    a = User.objects.create_user("p1", password="x" * 12)
    b = User.objects.create_user("p2", password="x" * 12)
    assert a.public_id != b.public_id
