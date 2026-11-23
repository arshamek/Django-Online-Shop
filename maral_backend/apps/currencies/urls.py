from django.urls import path

from .views import public, staff

urlpatterns = [
    path("currencies", public.CurrencyListView.as_view(), name="currencies"),
    path("exchange-rates", public.ExchangeRateListView.as_view(), name="exchange-rates"),
    path("staff/currencies", staff.CurrencyListView.as_view(), name="staff-currencies"),
    path("staff/currencies/health", staff.HealthView.as_view(), name="staff-currency-health"),
    path("staff/currencies/sources", staff.SourceListView.as_view(), name="staff-rate-sources"),
    path("staff/currencies/sources/<slug:code>", staff.SourceDetailView.as_view(), name="staff-rate-source"),
    path(
        "staff/currencies/sources/<slug:code>/refresh",
        staff.SourceRefreshView.as_view(),
        name="staff-rate-source-refresh",
    ),
    path("staff/currencies/routes", staff.RouteListView.as_view(), name="staff-rate-routes"),
    path("staff/currencies/routes/<int:pk>", staff.RouteDetailView.as_view(), name="staff-rate-route"),
    path("staff/currencies/rates", staff.RateHistoryView.as_view(), name="staff-rates"),
    path("staff/currencies/rates/<uuid:id>/review", staff.RateReviewView.as_view(), name="staff-rate-review"),
    path("staff/currencies/<str:code>", staff.CurrencyDetailView.as_view(), name="staff-currency"),
]
