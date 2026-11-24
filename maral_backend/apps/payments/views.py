from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.views.base import NoStoreMixin

from . import services
from .models import Payment
from .providers.base import WebhookRejected


class PaymentDetailView(NoStoreMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["payments"], summary="Payment status (ownership enforced)", responses={200: dict})
    def get(self, request, payment_id):
        payment = Payment.objects.filter(public_id=payment_id, user=request.user).first()
        if payment is None:
            raise NotFound()
        return Response(
            {"id": str(payment.public_id), "status": payment.status, "amount_irr": str(payment.amount_irr), "provider": payment.provider, "redirect_url": payment.redirect_url, "expires_at": payment.expires_at}
        )


class PaymentWebhookView(APIView):
    """Provider callback. Authenticated by the provider's signature, not by a session; browsers never call it."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "payment_webhook"

    @extend_schema(tags=["payments"], summary="Payment provider webhook (signature verified)", request=None, responses={200: dict})
    def post(self, request, provider, version=None):
        try:
            outcome = services.process_webhook(provider, request.headers, request.body)
        except WebhookRejected:
            return Response({"status": "rejected"}, status=400)
        return Response({"status": outcome})
