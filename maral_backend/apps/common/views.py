from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import defaults
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import health
from .api.responses import error_payload
from .api.serializers import ApiRootSerializer, LivenessSerializer, ReadinessSerializer

NO_CACHE = {"Cache-Control": "no-store"}


class _InfrastructureView(APIView):
    """Unauthenticated, unthrottled, unversioned: probes must work when everything else is broken."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes: list = []
    versioning_class = None
    pagination_class = None


class HealthView(_InfrastructureView):
    @extend_schema(operation_id="health_live", tags=["infrastructure"], responses=LivenessSerializer)
    def get(self, request):
        """Liveness: the process is up. Checks no dependencies."""
        return Response(
            {
                "status": "ok",
                "service": "maral-backend",
                "version": settings.APP_VERSION,
                "commit": settings.GIT_COMMIT,
                "time": timezone.now(),
            },
            headers=NO_CACHE,
        )


class ReadinessView(_InfrastructureView):
    @extend_schema(
        operation_id="health_ready",
        tags=["infrastructure"],
        responses={200: ReadinessSerializer, 503: ReadinessSerializer},
    )
    def get(self, request):
        """Readiness: database and Redis reachable. Celery workers are deliberately NOT part of web readiness."""
        results = health.run_readiness_checks()
        ready = all(result.status == "ok" for result in results.values())
        return Response(
            {"status": "ready" if ready else "not_ready", "checks": {name: r.as_dict() for name, r in results.items()}},
            status=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
            headers=NO_CACHE,
        )


class ApiRootView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(operation_id="api_root", tags=["infrastructure"], responses=ApiRootSerializer)
    def get(self, request, *args, **kwargs):
        """Confirms the versioned API is reachable."""
        return Response({"name": "MaralOnlineShop API", "version": request.version, "status": "ok"})


# ---- JSON error pages for API paths; Django's HTML defaults elsewhere (e.g. the admin) ----
def _is_api_path(request) -> bool:
    return request.path.startswith("/api/")


def _json_error(code: str, message: str, status_code: int) -> JsonResponse:
    return JsonResponse(error_payload(code, message), status=status_code, json_dumps_params={"ensure_ascii": False})


def not_found(request, exception=None):
    if _is_api_path(request):
        return _json_error("not_found", _("Not found."), 404)
    return defaults.page_not_found(request, exception)


def permission_denied(request, exception=None):
    if _is_api_path(request):
        return _json_error("permission_denied", _("You do not have permission to perform this action."), 403)
    return defaults.permission_denied(request, exception)


def bad_request(request, exception=None):
    if _is_api_path(request):
        return _json_error("bad_request", _("Bad request."), 400)
    return defaults.bad_request(request, exception)


def server_error(request):
    if _is_api_path(request):
        return _json_error("server_error", _("An unexpected error occurred."), 500)
    return defaults.server_error(request)
