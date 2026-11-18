import re

import pytest
from django.test import override_settings

pytestmark = pytest.mark.django_db


def test_liveness(api_client):
    response = api_client.get("/health/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["service"] == "maral-backend"
    assert response["Cache-Control"] == "no-store"


def test_api_root_is_versioned(api_client):
    response = api_client.get("/api/v1/")
    assert response.status_code == 200
    assert response.json() == {"name": "MaralOnlineShop API", "version": "v1", "status": "ok"}


def test_unknown_api_version_is_json_404(api_client):
    response = api_client.get("/api/v2/")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_unknown_api_path_is_json_404_with_request_id(api_client):
    response = api_client.get("/api/v1/nope/")
    body = response.json()
    assert response.status_code == 404
    assert body["error"]["code"] == "not_found"
    assert body["error"]["request_id"] == response["X-Request-ID"]


def test_readiness_ok(api_client, monkeypatch):
    from apps.common import health

    monkeypatch.setattr(health, "check_redis", lambda: health.CheckResult("ok", 1.0))
    response = api_client.get("/ready/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"]["status"] == "ok"


def test_readiness_reports_failure_without_leaking_details(api_client, monkeypatch):
    from apps.common import health

    monkeypatch.setattr(health, "check_redis", lambda: health.CheckResult("error", 2.0, "ConnectionError"))
    response = api_client.get("/ready/")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["redis"] == {"status": "error", "latency_ms": 2.0, "error": "ConnectionError"}
    assert "redis://" not in response.content.decode()


def test_ready_check_function_never_raises(monkeypatch):
    from apps.common import health

    def boom(url):
        raise RuntimeError("redis://:secret@host/0 exploded")

    monkeypatch.setattr(health, "ping_redis", boom)
    result = health.check_redis()
    assert result.status == "error"
    assert result.error == "RuntimeError"  # class name only - the message (with credentials) is dropped


def test_secure_headers(api_client):
    response = api_client.get("/api/v1/")
    assert response["X-Content-Type-Options"] == "nosniff"
    assert response["X-Frame-Options"] == "DENY"
    assert response["Referrer-Policy"] == "same-origin"


def test_json_only_renderer(api_client):
    response = api_client.get("/api/v1/", HTTP_ACCEPT="text/html")
    assert response.status_code == 406
    assert response.json()["error"]["code"] == "not_acceptable"


# ---- correlation ids -------------------------------------------------------
def test_request_id_generated(api_client):
    response = api_client.get("/api/v1/")
    assert re.fullmatch(r"[0-9a-f]{32}", response["X-Request-ID"])


def test_request_id_echoed_when_valid(api_client):
    response = api_client.get("/api/v1/", HTTP_X_REQUEST_ID="client-req-123")
    assert response["X-Request-ID"] == "client-req-123"


@pytest.mark.parametrize("bad", ["x" * 200, "has space", "line\\nbreak", "<script>"])
def test_request_id_rejected_when_unsafe(api_client, bad):
    response = api_client.get("/api/v1/", HTTP_X_REQUEST_ID=bad)
    assert response["X-Request-ID"] != bad
    assert re.fullmatch(r"[0-9a-f]{32}", response["X-Request-ID"])


# ---- CORS ------------------------------------------------------------------
@override_settings(CORS_ALLOWED_ORIGINS=["https://shop.example.com"])
def test_cors_allows_listed_origin(api_client):
    response = api_client.options(
        "/api/v1/",
        HTTP_ORIGIN="https://shop.example.com",
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="idempotency-key,x-request-id",
    )
    assert response["Access-Control-Allow-Origin"] == "https://shop.example.com"
    assert response["Access-Control-Allow-Credentials"] == "true"
    assert "idempotency-key" in response["Access-Control-Allow-Headers"]


@override_settings(CORS_ALLOWED_ORIGINS=["https://shop.example.com"])
def test_cors_blocks_unlisted_origin(api_client):
    response = api_client.get("/api/v1/", HTTP_ORIGIN="https://evil.example.com")
    assert "Access-Control-Allow-Origin" not in response
