"""Staff order management. Every change goes through the state machine / refund services and is audited."""

from decimal import Decimal

from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasStaffPermission, IsStaff
from apps.accounts.views.base import NoStoreMixin
from apps.audit import services as audit
from apps.catalog.context import money
from apps.common.api.pagination import StandardPagination
from apps.common.exceptions import BusinessRuleViolation
from apps.inventory.models import InventoryItem
from apps.inventory.services import stock

from .. import labels, state
from ..models import ActorType, Order, Refund
from ..services import refunds, transitions
from ..state import OrderStatus

TAG = ["staff"]


class StaffBase(NoStoreMixin, APIView):
    permission_classes = [IsAuthenticated, IsStaff, HasStaffPermission]
    parser_classes = [JSONParser]
    required_permissions = {
        "GET": "accounts.orders_view",
        "POST": "accounts.orders_manage",
    }


class RefundStaffBase(StaffBase):
    required_permissions = {"GET": "accounts.orders_view", "POST": "accounts.refunds_manage"}


def _order(ref) -> Order:
    order = Order.objects.select_related("user", "checkout").filter(public_id=ref).first()
    if order is None:
        raise NotFound()
    return order


def _out(order: Order) -> dict:
    return {
        "id": str(order.public_id),
        "code": order.code,
        "customer": {"id": str(order.user.public_id), "name": order.customer_name, "phone": order.customer_phone},
        "order_type": order.order_type,
        "status": order.status,
        "status_label": labels.label(order.status, "en"),
        "allowed_next": transitions.next_options(order),
        "total_irr": str(order.total_irr),
        "refunded_irr": str(order.refunded_irr),
        "refundable_irr": str(refunds.refundable_amount(order)),
        "placed_at": order.placed_at,
        "paid_at": order.paid_at,
        "items": [
            {
                "id": str(i.public_id),
                "sku": i.sku,
                "name": i.product_name,
                "quantity": i.quantity,
                "source": i.source_code,
                "source_product_id": i.source_product_id,
                "source_variant_id": i.source_variant_id,
                "source_product_url": i.source_product_url,
                "purchase_requirements": i.purchase_requirements,
                "accepted_unit_price_irr": str(i.unit_price_irr),
                "original": {"amount": str(i.original_amount), "currency": i.original_currency, "rate": str(i.exchange_rate)},
                "procurement": None
                if not hasattr(i, "procurement")
                else {"status": i.procurement.status, "source_order_ref": i.procurement.source_order_ref},
            }
            for i in order.items.select_related("procurement")
        ],
        "history": [
            {"from": h.from_status, "to": h.to_status, "actor_type": h.actor_type, "reason": h.reason, "metadata": h.metadata, "at": h.created_at}
            for h in order.history.all()
        ],
    }


class StaffOrdersView(StaffBase):
    @extend_schema(tags=TAG, summary="List orders (staff)", responses={200: dict})
    def get(self, request):
        qs = Order.objects.select_related("user")
        if request.query_params.get("status") in OrderStatus.values:
            qs = qs.filter(status=request.query_params["status"])
        if request.query_params.get("order_type") in ("DIRECT_INVENTORY", "PROXY_PURCHASE"):
            qs = qs.filter(order_type=request.query_params["order_type"])
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(
            [{"id": str(o.public_id), "code": o.code, "order_type": o.order_type, "status": o.status, "total_irr": str(o.total_irr), "placed_at": o.placed_at} for o in page]
        )


class StaffOrderView(StaffBase):
    @extend_schema(tags=TAG, summary="Order detail with full snapshot and history (staff)", responses={200: dict})
    def get(self, request, order_id):
        return Response(_out(_order(order_id)))


class TransitionIn(serializers.Serializer):
    to_status = serializers.ChoiceField(choices=OrderStatus.choices)
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    source_order_ref = serializers.CharField(max_length=120, required=False, allow_blank=True)
    tracking_carrier = serializers.CharField(max_length=60, required=False, allow_blank=True)
    tracking_number = serializers.CharField(max_length=80, required=False, allow_blank=True)


class StaffTransitionView(StaffBase):
    @extend_schema(tags=TAG, summary="Move an order along the state machine (invalid moves are refused)", request=TransitionIn, responses={200: dict})
    def post(self, request, order_id):
        data = TransitionIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        target = v["to_status"]
        # Refund states are reached only through the refund workflow so money and status cannot disagree.
        if target in (OrderStatus.REFUND_PENDING, OrderStatus.PARTIALLY_REFUNDED, OrderStatus.REFUNDED):
            raise BusinessRuleViolation("Use the refund workflow for refund statuses.", code="use_refund_workflow")
        if target == OrderStatus.PAID:
            raise BusinessRuleViolation("An order becomes PAID only through a verified payment.", code="use_payment_flow")
        meta = {k: v[k] for k in ("source_order_ref", "tracking_carrier", "tracking_number") if k in v}
        order = transitions.transition(_order(order_id), target, actor_type=ActorType.STAFF, actor=request.user, reason=v["reason"], metadata=meta)
        audit.record("order.status_changed", request=request, target=order, to_status=target)
        return Response(_out(order))


class RefundIn(serializers.Serializer):
    amount_irr = serializers.DecimalField(max_digits=18, decimal_places=0, min_value=1)
    reason = serializers.CharField(max_length=500)


class StaffOrderRefundView(RefundStaffBase):
    @extend_schema(tags=TAG, summary="Open a refund for an order (cancelled / delivered / partly refunded)", request=RefundIn, responses={201: dict})
    def post(self, request, order_id):
        data = RefundIn(data=request.data)
        data.is_valid(raise_exception=True)
        refund = refunds.staff_refund(_order(order_id), request.user, data.validated_data["amount_irr"], data.validated_data["reason"])
        audit.record("refund.opened", request=request, target=refund.order, amount_irr=str(refund.amount_irr))
        return Response(_refund_out(refund), status=201)


def _refund_out(r: Refund) -> dict:
    return {"id": str(r.public_id), "order": r.order.code, "kind": r.kind, "status": r.status, "amount_irr": str(r.amount_irr), "reason": r.reason, "resume_status": r.resume_status}


class StaffRefundsView(RefundStaffBase):
    @extend_schema(tags=TAG, summary="List refunds (staff)", responses={200: dict})
    def get(self, request):
        qs = Refund.objects.select_related("order")
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        paginator = StandardPagination()
        return paginator.get_paginated_response([_refund_out(r) for r in paginator.paginate_queryset(qs, request)])


class NoteIn(serializers.Serializer):
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    provider_ref = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")


def _refund(ref) -> Refund:
    r = Refund.objects.select_related("order").filter(public_id=ref).first()
    if r is None:
        raise NotFound()
    return r


class StaffRefundActionView(RefundStaffBase):
    action = ""

    @extend_schema(tags=TAG, summary="Approve / reject / complete / execute a refund", request=NoteIn, responses={200: dict})
    def post(self, request, refund_id):
        data = NoteIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        refund = _refund(refund_id)
        if self.action == "approve":
            refund = refunds.approve(refund, request.user, v["note"])
        elif self.action == "reject":
            refund = refunds.reject(refund, request.user, v["note"])
        elif self.action == "complete":
            refund = refunds.complete(refund, request.user, v["provider_ref"])
        elif self.action == "execute":
            refund = refunds.execute_with_provider(refund, request.user)
        audit.record(f"refund.{self.action}", request=request, target=refund.order, refund=str(refund.public_id))
        return Response(_refund_out(refund))


# --------------------------------------------------------------------------------------------- inventory
class InventoryStaffBase(StaffBase):
    required_permissions = {"GET": "accounts.catalog_manage", "POST": "accounts.catalog_manage"}


class StockIn(serializers.Serializer):
    variant_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, required=False)
    new_on_hand = serializers.IntegerField(min_value=0, required=False)
    reason = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


def _variant(public_id):
    from apps.catalog.models import ProductVariant

    v = ProductVariant.objects.filter(public_id=public_id).first()
    if v is None:
        raise NotFound()
    return v


class StaffStockReceiveView(InventoryStaffBase):
    @extend_schema(tags=TAG, summary="Receive stock for a variant", request=StockIn, responses={200: dict})
    def post(self, request, version=None):
        data = StockIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        if "quantity" not in v:
            raise BusinessRuleViolation("quantity is required.", code="invalid_quantity")
        item = stock.receive(_variant(v["variant_id"]), v["quantity"], reason=v["reason"] or "receipt", actor=request.user)
        audit.record("inventory.received", request=request, target=("variant", v["variant_id"]), quantity=v["quantity"])
        return Response({"on_hand": item.on_hand, "reserved": item.reserved, "sellable": item.sellable})


class StaffStockAdjustView(InventoryStaffBase):
    @extend_schema(tags=TAG, summary="Set on-hand stock after a count (reason required)", request=StockIn, responses={200: dict})
    def post(self, request, version=None):
        data = StockIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        if "new_on_hand" not in v:
            raise BusinessRuleViolation("new_on_hand is required.", code="invalid_quantity")
        variant = _variant(v["variant_id"])
        item = InventoryItem.objects.filter(variant=variant).order_by("id").first()
        if item is None:
            raise BusinessRuleViolation("This variant has no stock record yet; receive stock first.", code="no_inventory_item")
        item = stock.adjust(item, v["new_on_hand"], reason=v["reason"], actor=request.user)
        audit.record("inventory.adjusted", request=request, target=("variant", v["variant_id"]), new_on_hand=v["new_on_hand"])
        return Response({"on_hand": item.on_hand, "reserved": item.reserved, "sellable": item.sellable})
