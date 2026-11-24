from django.urls import path

from . import views

urlpatterns = [
    path("payments/<uuid:payment_id>", views.PaymentDetailView.as_view(), name="payment-detail"),
    path("webhooks/payments/<slug:provider>", views.PaymentWebhookView.as_view(), name="payment-webhook"),
]
