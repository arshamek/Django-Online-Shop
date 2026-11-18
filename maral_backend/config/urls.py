from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from apps.common import views as common_views

handler400 = "apps.common.views.bad_request"
handler403 = "apps.common.views.permission_denied"
handler404 = "apps.common.views.not_found"
handler500 = "apps.common.views.server_error"

urlpatterns = [
    # Infrastructure endpoints: unversioned, for load balancers / orchestrators.
    path("health/", common_views.HealthView.as_view(), name="health"),
    path("ready/", common_views.ReadinessView.as_view(), name="ready"),
    # Versioned API. Only `v1` exists; /api/v2/ is a 404 until it does.
    re_path(r"^api/(?P<version>v1)/", include("config.api_v1")),
    path(settings.ADMIN_URL, admin.site.urls),
]

if settings.API_DOCS_ENABLED:
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
        path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    ]
