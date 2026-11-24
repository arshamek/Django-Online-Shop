"""Customer checkout and order APIs. Ownership is part of every query: someone else's id is a plain 404."""

import uuid
from decimal import Decimal

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.permissions import CsrfRequired
from apps.accounts.views.base import NoStoreMixin
from apps.catalog.context import get_context
from apps.common.api.pagination import StandardPagination
from apps.common.exceptions import BusinessRuleViolation, DomainError
from apps.payments import services as payment_services

from .. import presenters
from ..models import Checkout, Order, Refund
from ..services import checkout as checkout_service
from ..services import idempotency, lifecycle, placing, refunds
from ..state import OrderStatus

TAG = ["orders"]


class CustomerBase(NoStoreMixin, APIView):
    permission_classes = [IsAuthenticated, CsrfRequired]
    parser_classes = [JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "checkout"


def get_order(request, ref) -> Order:
    qs = Order.objects.filter(user=request.user).select_related("quote__rule_set", "checkout")
    try:
        order = qs.filter(public_id=uuid.UUID(str(ref))).first()
    except ValueError:
        order = qs.filter(code=str(ref).upper()).first()
    if order is None:
        raise NotFound()
    return order


def get_checkout(request, public_id) -> Checkout:
    checkout = Checkout.objects.filter(public_id=public_id, user=request.user).select_related("quote", "address").first()
    if checkout is None:
        raise NotFound()
    return checkout


# --------------------------------------------------------------------------------------------- checkout
class CheckoutIn(serializers.Serializer):
    address_id = serializers.UUIDField()
    shipping_method = serializers.SlugField(max_length=40)
    currency = serializers.RegexField(r"^[A-Z]{3}$", default="IRT")
    # No price, total, discount, shipping fee or status field exists: whatever a client adds is dropped.


class CheckoutPreviewView(CustomerBase):
    @extend_schema(tags=TAG, summary="Preview the order summary (nothing is saved)", request=CheckoutIn, responses={200: dict})
    def post(self, request):
        data = CheckoutIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        return Response(checkout_service.preview(request.user, address_id=v["address_id"], shipping_method=v["shipping_method"], currency=v["currency"]))


class CheckoutCreateView(CustomerBase):
    @extend_schema(tags=TAG, summary="Create a checkout: validates, builds the authoritative price quote and totals", request=CheckoutIn, responses={201: dict})
    def post(self, request):
        data = CheckoutIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        checkout = checkout_service.build_checkout(request.user, address_id=v["address_id"], shipping_method=v["shipping_method"], currency=v["currency"])
        return Response(checkout_service.present(checkout), status=201)


class CheckoutDetailView(CustomerBase):
    @extend_schema(tags=TAG, summary="Retrieve a checkout", responses={200: dict})
    def get(self, request, checkout_id):
        return Response(checkout_service.present(get_checkout(request, checkout_id)))


class CheckoutPaymentView(CustomerBase):
    @extend_schema(tags=TAG, summary="Start (or resume) payment for a checkout's unpaid orders. Header: Idempotency-Key", request=None, responses={201: dict})
    def post(self, request, checkout_id):
        checkout = get_checkout(request, checkout_id)
        payment = payment_services.initiate(checkout, request.user, idempotency_key=request.headers.get("Idempotency-Key", "").strip())
        return Response(payment_out(payment), status=201)


def payment_out(payment) -> dict:
    return {
        "id": str(payment.public_id),
        "provider": payment.provider,
        "status": payment.status,
        "amount_irr": str(payment.amount_irr),
        "redirect_url": payment.redirect_url,
        "expires_at": payment.expires_at,
    }


# --------------------------------------------------------------------------------------------- orders
class OrderCreateIn(serializers.Serializer):
    checkout_id = serializers.UUIDField()
    accepted_total_irr = serializers.DecimalField(max_digits=18, decimal_places=0, required=False)
    initiate_payment = serializers.BooleanField(default=True)


class OrdersView(CustomerBase):
    @property
    def throttle_scope(self):
        return "order_create" if self.request.method == "POST" else "checkout"

    @extend_schema(
        tags=TAG,
        summary="Create the order(s) from a checkout (one per fulfilment type). Header Idempotency-Key is required",
        request=OrderCreateIn,
        responses={201: dict},
    )
    def post(self, request):
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key or len(key) > 80:
            raise BusinessRuleViolation("Send an Idempotency-Key header (max 80 characters).", code="idempotency_key_required")
        data = OrderCreateIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        ctx = get_context(request)

        def work():
            created = placing.create_orders(request.user, checkout_id=v["checkout_id"], accepted_total_irr=v.get("accepted_total_irr"))
            body = {"checkout_id": str(v["checkout_id"]), "orders": [presenters.order_summary(o, ctx) for o in created]}
            return 201, body

        status, body, replayed = idempotency.run(request.user, "order.create", key, idempotency.hash_request({"c": str(v["checkout_id"]), "t": v.get("accepted_total_irr")}), work)
        payment, payment_error = None, None
        if v["initiate_payment"]:
            try:
                payment = payment_out(payment_services.initiate(get_checkout(request, v["checkout_id"]), request.user, idempotency_key=f"order:{key}"[:80]))
            except DomainError as exc:  # orders exist and stay payable; the client can retry via the checkout payment endpoint
                payment_error = exc.get_codes() if hasattr(exc, "get_codes") else "payment_unavailable"
        response = Response({**body, "payment": payment, "payment_error": payment_error}, status=200 if replayed else status)
        if replayed:
            response["Idempotent-Replay"] = "true"
        return response

    @extend_schema(
        tags=TAG,
        summary="List my orders",
        parameters=[OpenApiParameter("status", str, required=False), OpenApiParameter("order_type", str, required=False)],
        responses={200: dict},
    )
    def get(self, request):
        ctx = get_context(request)
        qs = Order.objects.filter(user=request.user).prefetch_related("items")
        if request.query_params.get("status") in OrderStatus.values:
            qs = qs.filter(status=request.query_params["status"])
        if request.query_params.get("order_type") in ("DIRECT_INVENTORY", "PROXY_PURCHASE"):
            qs = qs.filter(order_type=request.query_params["order_type"])
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response([presenters.order_summary(o, ctx) for o in page])


class OrderDetailView(CustomerBase):
    @extend_schema(tags=TAG, summary="Retrieve an order with its price snapshot", responses={200: dict})
    def get(self, request, ref):
        return Response(presenters.order_detail(get_order(request, ref), get_context(request)))


class OrderTrackingView(CustomerBase):
    @extend_schema(tags=TAG, summary="Order tracking (progress steps, events, estimate)", responses={200: dict})
    def get(self, request, ref):
        return Response(presenters.tracking(get_order(request, ref), get_context(request)))


class OrderHistoryView(CustomerBase):
    @extend_schema(tags=TAG, summary="Status history of an order", responses={200: dict})
    def get(self, request, ref):
        return Response({"results": presenters.history(get_order(request, ref), get_context(request))})


class CancelIn(serializers.Serializer):
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class OrderCancelView(CustomerBase):
    @extend_schema(tags=TAG, summary="Request cancellation (allowed until packing / purchasing starts)", request=CancelIn, responses={200: dict})
    def post(self, request, ref):
        data = CancelIn(data=request.data)
        data.is_valid(raise_exception=True)
        order = get_order(request, ref)
        lifecycle.customer_cancel(order, request.user, data.validated_data["reason"])
        order = get_order(request, ref)
        return Response(presenters.order_detail(order, get_context(request)))


class RefundLineIn(serializers.Serializer):
    order_item_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, max_value=999)


class RefundIn(serializers.Serializer):
    items = RefundLineIn(many=True, allow_empty=False)
    reason = serializers.CharField(max_length=500)
    # The amount is NOT an input: it is computed from the order's price snapshot.


class OrderRefundsView(CustomerBase):
    @extend_schema(tags=TAG, summary="List refund requests of an order", responses={200: dict})
    def get(self, request, ref):
        order = get_order(request, ref)
        cur = get_context(request).currency
        return Response({"results": [presenters.refund_out(r, cur) for r in order.refunds.all()]})

    @extend_schema(tags=TAG, summary="Request a refund (return) for delivered items", request=RefundIn, responses={201: dict})
    def post(self, request, ref):
        data = RefundIn(data=request.data)
        data.is_valid(raise_exception=True)
        order = get_order(request, ref)
        refund = refunds.request_return(
            order, request.user, [(i["order_item_id"], i["quantity"]) for i in data.validated_data["items"]], data.validated_data["reason"]
        )
        return Response(presenters.refund_out(refund, get_context(request).currency), status=201)
