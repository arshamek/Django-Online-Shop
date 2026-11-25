from django.urls import include, path
from rest_framework.routers import SimpleRouter

from . import staff_views, views

staff_router = SimpleRouter(trailing_slash=False)
staff_router.register(
    "staff/payment-providers", staff_views.PaymentProviderConfigViewSet, basename="staff-payment-provider"
)

urlpatterns = [
    path("payments/methods", views.PaymentMethodsView.as_view(), name="payment-methods"),
    path("payments/<uuid:payment_id>", views.PaymentDetailView.as_view(), name="payment-detail"),
    path("payments/<uuid:payment_id>/cancel", views.PaymentCancelView.as_view(), name="payment-cancel"),
    path("payments/callback/<slug:provider>", views.PaymentReturnView.as_view(), name="payment-return"),
    path("webhooks/payments/<slug:provider>", views.PaymentWebhookView.as_view(), name="payment-webhook"),
    # staff
    path("staff/payments", staff_views.StaffPaymentsView.as_view(), name="staff-payments"),
    path(
        "staff/payments/reconciliation",
        staff_views.StaffReconciliationView.as_view(),
        name="staff-payment-reconciliation",
    ),
    path("staff/payments/<uuid:payment_id>", staff_views.StaffPaymentView.as_view(), name="staff-payment"),
    path(
        "staff/payments/<uuid:payment_id>/verify",
        staff_views.StaffPaymentVerifyView.as_view(),
        name="staff-payment-verify",
    ),
    path(
        "staff/payments/<uuid:payment_id>/resolve-review",
        staff_views.StaffPaymentResolveView.as_view(),
        name="staff-payment-resolve",
    ),
    path("", include(staff_router.urls)),
]
