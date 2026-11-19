from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from ..permissions import CsrfRequired


class NoStoreMixin:
    """
    Account data must never be cached by browsers or shared caches.
    Also swallows the `version` URL kwarg (URLPathVersioning) so handlers keep clean signatures.
    """

    def dispatch(self, request, *args, **kwargs):
        self._url_version = kwargs.pop("version", None)
        return super().dispatch(request, *args, **kwargs)

    def determine_version(self, request, *args, **kwargs):
        url_version = getattr(self, "_url_version", None)  # absent when drf-spectacular introspects the view
        if url_version is not None:
            kwargs["version"] = url_version
        return super().determine_version(request, *args, **kwargs)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        response["Vary"] = "Cookie"
        return response


class PublicAuthView(NoStoreMixin, APIView):
    """Anonymous-callable POST endpoint: CSRF enforced, throttled per client IP by `throttle_scope`."""

    permission_classes = [AllowAny, CsrfRequired]
    throttle_classes = [ScopedRateThrottle]


class PrivateView(NoStoreMixin, APIView):
    permission_classes = [IsAuthenticated]


def documented(**kwargs):
    return extend_schema(**kwargs)
