import pytest
from django.urls import path
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework.views import APIView

from apps.common.exceptions import BusinessRuleViolation, Conflict

pytestmark = pytest.mark.django_db


class _Payload(serializers.Serializer):
    phone = serializers.CharField()
    quantity = serializers.IntegerField(min_value=1)


class ValidationView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        s = _Payload(data=request.data)
        s.is_valid(raise_exception=True)
        return Response(s.data)


class ConflictView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        raise Conflict("Not enough stock.", code="out_of_stock")


class RuleView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        raise BusinessRuleViolation("Address not serviceable.", code="address_not_serviceable")


class BoomView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        raise RuntimeError("secret internal detail: password=hunter2")


class PrivateView(APIView):
    def get(self, request):
        return Response({"ok": True})


urlpatterns = [
    path("t/validation/", ValidationView.as_view()),
    path("t/conflict/", ConflictView.as_view()),
    path("t/rule/", RuleView.as_view()),
    path("t/boom/", BoomView.as_view()),
    path("t/private/", PrivateView.as_view()),
]


@pytest.fixture(autouse=True)
def _urls(settings):
    settings.ROOT_URLCONF = __name__


@pytest.fixture
def client_():
    return APIClient(raise_request_exception=False)


def test_validation_error_envelope(client_):
    response = client_.post("/t/validation/", {"quantity": 0}, format="json")
    body = response.json()
    assert response.status_code == 400
    assert body["error"]["code"] == "validation_error"
    assert set(body["error"]["fields"]) == {"phone", "quantity"}
    assert body["error"]["request_id"] == response["X-Request-ID"]


def test_malformed_json_is_parse_error(client_):
    response = client_.post("/t/validation/", "{not json", content_type="application/json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "parse_error"


def test_unsupported_media_type(client_):
    response = client_.post("/t/validation/", "a=b", content_type="application/x-www-form-urlencoded")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"


def test_domain_conflict_uses_custom_code(client_):
    response = client_.get("/t/conflict/")
    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "out_of_stock",
        "message": "Not enough stock.",
        "request_id": response["X-Request-ID"],
    }


def test_business_rule_violation(client_):
    response = client_.get("/t/rule/")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "address_not_serviceable"


def test_unauthenticated_is_401(client_):
    response = client_.get("/t/private/")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"
    assert response["WWW-Authenticate"].startswith("Session")


def test_csrf_failure_code():
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user("csrf-user", password="x" * 12)
    client = APIClient(enforce_csrf_checks=True, raise_request_exception=False)
    client.force_login(user)
    response = client.post("/t/validation/", {"phone": "1", "quantity": 1}, format="json")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


def test_unhandled_exception_hides_internals(client_):
    response = client_.get("/t/boom/")
    body = response.json()
    assert response.status_code == 500
    assert body["error"]["code"] == "server_error"
    assert "hunter2" not in response.content.decode()
    assert body["error"]["request_id"]


def test_method_not_allowed(client_):
    response = client_.delete("/t/conflict/")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_throttling_returns_429_with_retry_after(settings, client_):
    from django.core.cache import cache
    from rest_framework.settings import api_settings

    cache.clear()
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
        "DEFAULT_THROTTLE_RATES": {"anon": "2/min", "user": "2/min"},
    }
    assert api_settings.DEFAULT_THROTTLE_CLASSES  # settings reloaded
    # ApiView reads throttle classes at class-definition time, so use a fresh view
    from rest_framework.throttling import AnonRateThrottle

    class Throttled(APIView):
        permission_classes = [AllowAny]
        throttle_classes = [AnonRateThrottle]

        def get(self, request):
            return Response({})

    from rest_framework.test import APIRequestFactory

    factory = APIRequestFactory()
    view = Throttled.as_view()
    codes = [view(factory.get("/x/")).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    last = view(factory.get("/x/"))
    last.render()
    assert last["Retry-After"]
    assert last.data["error"]["code"] == "rate_limited"
