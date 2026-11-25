"""Customer payment API + provider callbacks."""

from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpResponseRedirect
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.permissions import CsrfRequired
from apps.accounts.views.base import NoStoreMixin
from apps.catalog.context import get_context
from apps.common.exceptions import BusinessRuleViolation

from . import methods, services
from .models import Payment
from .presenters import payment_out
from .providers.base import CallbackRejected


class PaymentMethodsView(NoStoreMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["payments"], summary="Payment methods usable for a checkout, with the exact amount each would charge",
        parameters=[OpenApiParameter("checkout_id", str, required=True)], responses={200: dict},
    )  # fmt: skip
    def get(self, request):
        from apps.orders.models import Checkout

        checkout = (
            Checkout.objects.filter(public_id=request.query_params.get("checkout_id", ""), user=request.user).first()
            if _is_uuid(request.query_params.get("checkout_id", ""))
            else None
        )
        if checkout is None:
            raise NotFound()
        orders = services.payable_orders(checkout)
        if not orders:
            raise BusinessRuleViolation("Nothing is waiting for payment.", code="nothing_to_pay")
        amount = sum(o.total_irr for o in orders)
        ctx = get_context(request)
        found = methods.list_methods(
            amount_irr=amount,
            display_currency=checkout.display_currency,
            country=(checkout.address_snapshot or {}).get("country", ""),
            lang=ctx.lang,
        )
        return Response({"checkout_id": str(checkout.public_id), "amount_irr": str(amount), "methods": found})


def _is_uuid(value: str) -> bool:
    import uuid

    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


class PaymentDetailView(NoStoreMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["payments"],
        summary="Payment status (ownership enforced). The server's record is the only truth",
        responses={200: dict},
    )
    def get(self, request, payment_id):
        payment = Payment.objects.filter(public_id=payment_id, user=request.user).first()
        if payment is None:
            raise NotFound()
        return Response(payment_out(payment))


class PaymentCancelView(NoStoreMixin, APIView):
    permission_classes = [IsAuthenticated, CsrfRequired]

    @extend_schema(
        tags=["payments"],
        summary="Abandon a payment in progress (so another method can be used)",
        request=None,
        responses={200: dict},
    )
    def post(self, request, payment_id):
        payment = Payment.objects.filter(public_id=payment_id, user=request.user).first()
        if payment is None:
            raise NotFound()
        return Response(payment_out(services.cancel_payment(payment, request.user)))


def _remote_ip(request) -> str | None:
    return request.META.get("REMOTE_ADDR") or None


class PaymentWebhookView(APIView):
    """Server-to-server provider notification. Authenticated by the provider's signature, never by a session."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "payment_webhook"

    @extend_schema(
        tags=["payments"], summary="Payment provider webhook (signature verified)", request=None, responses={200: dict}
    )
    def post(self, request, provider, version=None):
        try:
            outcome = services.process_callback(
                provider,
                headers=request.headers,
                body=request.body,
                query=request.query_params,
                remote_ip=_remote_ip(request),
            )
        except CallbackRejected:
            return Response({"status": "rejected"}, status=400)
        return Response({"status": outcome})


class PaymentReturnView(APIView):
    """
    Where the CUSTOMER'S BROWSER comes back from the provider's page. Whatever the query string claims is only a hint:
    the service confirms it with the provider server-side. The redirect carries no status, only the payment id, so a
    forged URL cannot make the storefront show "paid"; the page must read GET /payments/<id>.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "payment_webhook"

    @extend_schema(
        tags=["payments"],
        summary="Browser return from the provider (verified server-side, then redirects)",
        request=None,
        responses={302: None},
    )
    def get(self, request, provider, version=None):
        return self._handle(request, provider)

    @extend_schema(
        tags=["payments"],
        summary="Browser return via POST (some gateways post back)",
        request=None,
        responses={302: None},
    )
    def post(self, request, provider, version=None):
        return self._handle(request, provider)

    def _handle(self, request, provider):
        target = f"{settings.FRONTEND_BASE_URL}{settings.PAYMENTS_RESULT_PATH}"
        payment_id = ""
        try:
            services.process_callback(
                provider, headers=request.headers, body=request.body, query={**request.query_params.dict(), **(request.POST.dict() if request.method == "POST" else {})},
                method=request.method, remote_ip=_remote_ip(request),
            )  # fmt: skip
        except CallbackRejected:
            return HttpResponseRedirect(f"{target}?{urlencode({'error': 'invalid_callback'})}")
        ref = request.query_params.get("m", "")
        if _is_uuid_hex(ref):
            payment = Payment.objects.filter(public_id=ref).first()
            payment_id = str(payment.public_id) if payment else ""
        return HttpResponseRedirect(
            f"{target}?{urlencode({'payment': payment_id})}"
            if payment_id
            else f"{target}?{urlencode({'error': 'unknown_payment'})}"
        )


def _is_uuid_hex(value: str) -> bool:
    return len(value) == 32 and all(c in "0123456789abcdef" for c in value.lower())
