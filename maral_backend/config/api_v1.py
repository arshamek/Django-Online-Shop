"""URL table for /api/v1/. Business apps register their routes here in later phases."""

from django.urls import include, path

from apps.common.views import ApiRootView

app_name = "v1"

urlpatterns = [
    path("", ApiRootView.as_view(), name="root"),
    path("", include("apps.accounts.urls")),
    path("", include("apps.catalog.urls")),
    path("", include("apps.imports.urls")),
    path("", include("apps.currencies.urls")),
    path("", include("apps.pricing.urls")),
    path("", include("apps.translations.urls")),
    # Phase 7
    path("", include("apps.cart.urls")),
    path("", include("apps.wishlist.urls")),
    path("", include("apps.orders.urls")),
    path("", include("apps.payments.urls")),
    path("", include("apps.shipping.urls")),
]
