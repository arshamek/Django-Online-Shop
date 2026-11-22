import pytest
from django.core.cache import cache
from django.db import transaction
from rest_framework.test import APIClient

PASSWORD = "Correct-Horse-9x"
API = "/api/v1"


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    cache.clear()
    from apps.accounts.delivery import LocmemSMSBackend

    LocmemSMSBackend.outbox.clear()
    # run on_commit hooks immediately (pytest wraps tests in a transaction that never commits)
    monkeypatch.setattr(transaction, "on_commit", lambda fn, using=None, robust=False: fn())


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def make_user(db):
    from apps.accounts.models import CustomerProfile, User

    counter = {"n": 0}

    def _make(email="auto", phone=None, password=PASSWORD, **extra):
        counter["n"] += 1
        if email == "auto":
            email = f"user{counter['n']}@example.com"
        user = User.objects.create_user(email=email, phone=phone, password=password, **extra)
        CustomerProfile.objects.get_or_create(user=user)
        return user

    return _make


@pytest.fixture
def make_staff(make_user):
    from django.contrib.auth.models import Group

    def _make(role=None, superuser=False, **extra):
        user = make_user(is_staff=True, is_superuser=superuser, **extra)
        if role:
            user.groups.add(Group.objects.get(name=role))
        return user

    return _make


def new_client(csrf=False):
    return APIClient(enforce_csrf_checks=csrf)


@pytest.fixture
def login(db):
    """login(user) -> APIClient signed in through the real login endpoint."""

    def _login(user, password=PASSWORD, csrf=False):
        client = new_client(csrf)
        if csrf:
            token = client.get(f"{API}/auth/csrf").json()["csrf_token"]
            client.defaults["HTTP_X_CSRFTOKEN"] = token
        identifier = user.email or user.phone
        response = client.post(f"{API}/auth/login", {"identifier": identifier, "password": password}, format="json")
        assert response.status_code == 200, response.content
        if csrf:
            client.defaults["HTTP_X_CSRFTOKEN"] = response.json()["csrf_token"]
        return client

    return _login


@pytest.fixture
def outbox():
    from django.core import mail

    mail.outbox.clear()
    return mail.outbox


# ---------------------------------------------------------------- catalogue (Phase 4)
@pytest.fixture
def demo_catalog(db):
    """The 16 fictitious products of the frontend mock (apps/catalog/demo.py)."""
    from apps.catalog.demo import load_demo_catalog

    return load_demo_catalog()


@pytest.fixture
def catalog_api(api_client):
    def get(path, **params):
        return api_client.get(f"{API}{path}", params or None)

    return get


@pytest.fixture
def no_enqueue(monkeypatch):
    """Import jobs are driven by hand in tests; record what would have been queued instead of running it."""
    queued = []
    from apps.imports.services import jobs

    monkeypatch.setattr(jobs, "_enqueue", lambda job, countdown=0: queued.append((job.pk, countdown)))
    return queued
