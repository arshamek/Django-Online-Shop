"""Staff shipping administration: zones, methods, rates, country policies; shipments, tracking and history."""

import json
import re
from decimal import Decimal

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasStaffPermission, IsStaff
from apps.accounts.views.base import NoStoreMixin
from apps.audit import services as audit
from apps.common.api.pagination import StandardPagination
from apps.orders.models import Order

from . import addresses
from .models import CountryShippingPolicy, Shipment, ShipmentStatus, ShippingMethod, ShippingRate, ShippingZone
from .presenters import shipment_out
from .providers import registry
from .services import shipments

TAG = ["staff"]
CONFIG_PERMISSIONS = {
    "GET": "accounts.orders_view", "POST": "accounts.shipping_manage", "PATCH": "accounts.shipping_manage",
    "PUT": "accounts.shipping_manage", "DELETE": "accounts.shipping_manage",
}  # fmt: skip


# ------------------------------------------------------------------------------------------------ configuration
class ConfigViewSet(NoStoreMixin, viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsStaff, HasStaffPermission]
    parser_classes = [JSONParser]
    required_permissions = CONFIG_PERMISSIONS
    http_method_names = [
        "get",
        "post",
        "patch",
        "head",
        "options",
    ]  # deactivate instead of delete (orders reference them)
    audit_name = "shipping_config"

    def perform_create(self, serializer):
        obj = serializer.save()
        audit.record(f"{self.audit_name}.created", request=self.request, target=obj, id=obj.pk)

    def perform_update(self, serializer):
        obj = serializer.save()
        audit.record(
            f"{self.audit_name}.updated",
            request=self.request,
            target=obj,
            id=obj.pk,
            fields=sorted(serializer.validated_data),
        )


def _check_days(attrs, instance=None):
    low = attrs.get("delivery_min_days", getattr(instance, "delivery_min_days", None))
    high = attrs.get("delivery_max_days", getattr(instance, "delivery_max_days", None))
    if low is not None and high is not None and low > high:
        raise serializers.ValidationError({"delivery_max_days": "Must not be smaller than the minimum."})


def _iso_list(value, field):
    out = []
    for code in value:
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z]{2}", code):
            raise serializers.ValidationError({field: f"Invalid ISO country code: {code!r}"})
        out.append(code.upper())
    return out


class ZoneSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingZone
        fields = ["id", "code", "names", "countries", "regions", "priority", "is_active"]

    def validate_countries(self, value):
        if not value:
            raise serializers.ValidationError("At least one country is required.")
        return _iso_list(value, "countries")


class ZoneViewSet(ConfigViewSet):
    queryset = ShippingZone.objects.all()
    serializer_class = ZoneSerializer
    audit_name = "shipping_zone"


class MethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = ShippingMethod
        fields = [
            "id", "code", "names", "descriptions", "kind", "provider_key", "carrier_code", "fee_irr", "free_over_irr", "value_status", "countries",
            "delivery_min_days", "delivery_max_days", "delivery_estimate_status", "requires_tracking", "tracking_url_template", "max_weight_grams",
            "config", "is_active", "sort_order",
        ]  # fmt: skip
        extra_kwargs = {"fee_irr": {"required": False, "default": Decimal(0)}}

    def validate_provider_key(self, value):
        if registry.get(value) is None:
            raise serializers.ValidationError("No shipping adapter with this key is installed.")
        return value

    def validate_tracking_url_template(self, value):
        if value and (not value.startswith("https://") or "{tracking_number}" not in value):
            raise serializers.ValidationError("Use an https URL containing {tracking_number}.")
        return value

    def validate_countries(self, value):
        return _iso_list(value, "countries")

    def validate(self, attrs):
        _check_days(attrs, self.instance)
        return attrs


class MethodViewSet(ConfigViewSet):
    queryset = ShippingMethod.objects.all()
    serializer_class = MethodSerializer
    lookup_field = "code"
    lookup_value_regex = "[-a-zA-Z0-9_]+"
    audit_name = "shipping_method"


class RateSerializer(serializers.ModelSerializer):
    method = serializers.SlugRelatedField(slug_field="code", queryset=ShippingMethod.objects.all())
    zone = serializers.SlugRelatedField(slug_field="code", queryset=ShippingZone.objects.all())

    class Meta:
        model = ShippingRate
        fields = [
            "id", "method", "zone", "base_fee_irr", "per_kg_irr", "included_weight_grams", "min_subtotal_irr", "max_subtotal_irr", "free_over_irr",
            "value_status", "delivery_min_days", "delivery_max_days", "is_active", "sort_order",
        ]  # fmt: skip

    def validate(self, attrs):
        _check_days(attrs, self.instance)
        low = attrs.get("min_subtotal_irr", getattr(self.instance, "min_subtotal_irr", Decimal(0)))
        high = attrs.get("max_subtotal_irr", getattr(self.instance, "max_subtotal_irr", None))
        if high is not None and high <= low:
            raise serializers.ValidationError({"max_subtotal_irr": "Must be greater than the minimum."})
        return attrs


class RateViewSet(ConfigViewSet):
    queryset = ShippingRate.objects.select_related("method", "zone")
    serializer_class = RateSerializer
    audit_name = "shipping_rate"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]  # a rate row can be removed outright


class CountryPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = CountryShippingPolicy
        fields = [
            "id", "country", "names", "is_shippable", "address_requirements", "customs_mode", "customs_notice", "taxes_notice", "policy_notes",
            "restricted_category_slugs", "default_display_currency", "is_active", "updated_at",
        ]  # fmt: skip
        read_only_fields = ["updated_at"]

    def validate_country(self, value):
        if not re.fullmatch(r"[A-Za-z]{2}", value):
            raise serializers.ValidationError("Use an ISO 3166-1 alpha-2 code.")
        return value.upper()

    def validate_address_requirements(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Must be an object.")
        unknown = set(value) - {"required", "optional", "postal_code_pattern", "phone_required"}
        if unknown:
            raise serializers.ValidationError(f"Unknown keys: {sorted(unknown)}")
        for key in ("required", "optional"):
            bad = [f for f in value.get(key, []) if f not in addresses.KNOWN_FIELDS]
            if bad:
                raise serializers.ValidationError(f"Unknown address fields in '{key}': {bad}")
        pattern = value.get("postal_code_pattern")
        if pattern is not None:
            if not isinstance(pattern, str) or len(pattern) > addresses.MAX_PATTERN_LENGTH:
                raise serializers.ValidationError("postal_code_pattern must be a short string.")
            try:
                re.compile(pattern)
            except re.error:
                raise serializers.ValidationError("postal_code_pattern is not a valid regular expression.") from None
        return json.loads(json.dumps(value))


class CountryPolicyViewSet(ConfigViewSet):
    queryset = CountryShippingPolicy.objects.all()
    serializer_class = CountryPolicySerializer
    lookup_field = "country"
    lookup_value_regex = "[A-Za-z]{2}"
    audit_name = "shipping_country_policy"


# ------------------------------------------------------------------------------------------------ shipments
class ShipmentBase(NoStoreMixin, APIView):
    permission_classes = [IsAuthenticated, IsStaff, HasStaffPermission]
    parser_classes = [JSONParser]
    required_permissions = {"GET": "accounts.orders_view", "POST": "accounts.shipments_manage"}


def _shipment(public_id) -> Shipment:
    obj = Shipment.objects.select_related("order").prefetch_related("events").filter(public_id=public_id).first()
    if obj is None:
        raise NotFound()
    return obj


def _order(public_id) -> Order:
    obj = Order.objects.filter(public_id=public_id).first()
    if obj is None:
        raise NotFound()
    return obj


class StaffShipmentsView(ShipmentBase):
    @extend_schema(tags=TAG, summary="Shipments (?status=, ?open=1, ?order=CODE)", responses={200: dict})
    def get(self, request):
        qs = Shipment.objects.select_related("order").prefetch_related("events")
        p = request.query_params
        if p.get("status") in ShipmentStatus.values:
            qs = qs.filter(status=p["status"])
        if p.get("open") in ("1", "true"):
            qs = qs.exclude(status__in=shipments.TERMINAL)
        if p.get("order"):
            qs = qs.filter(order__code=p["order"].upper())
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response([shipment_out(s, "en", staff=True) for s in page])


class StaffShipmentView(ShipmentBase):
    @extend_schema(tags=TAG, summary="Shipment with its full history", responses={200: dict})
    def get(self, request, shipment_id):
        return Response(shipment_out(_shipment(shipment_id), "en", staff=True))


class OrderShipmentIn(serializers.Serializer):
    carrier = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    tracking_number = serializers.CharField(max_length=80, required=False, allow_blank=True, default="")
    use_provider = serializers.BooleanField(default=False)


class StaffOrderShipmentsView(ShipmentBase):
    @extend_schema(tags=TAG, summary="Shipments of an order", responses={200: dict})
    def get(self, request, order_id):
        order = _order(order_id)
        return Response(
            [
                shipment_out(s, "en", staff=True)
                for s in order.shipments.select_related("order").prefetch_related("events")
            ]
        )

    @extend_schema(
        tags=TAG,
        summary="Create a shipment for an order (optionally via the carrier adapter)",
        request=OrderShipmentIn,
        responses={201: dict},
    )
    def post(self, request, order_id):
        data = OrderShipmentIn(data=request.data)
        data.is_valid(raise_exception=True)
        shipment = shipments.create_shipment(_order(order_id), actor=request.user, **data.validated_data)
        return Response(shipment_out(_shipment(shipment.public_id), "en", staff=True), status=201)


class StatusIn(serializers.Serializer):
    status = serializers.ChoiceField(choices=ShipmentStatus.choices)
    description = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")
    location = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    occurred_at = serializers.DateTimeField(required=False)


class StaffShipmentStatusView(ShipmentBase):
    @extend_schema(
        tags=TAG,
        summary="Move a shipment along its status machine (invalid moves refused)",
        request=StatusIn,
        responses={200: dict},
    )
    def post(self, request, shipment_id):
        data = StatusIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        shipments.apply_status(
            _shipment(shipment_id),
            v["status"],
            actor=request.user,
            description=v["description"],
            location=v["location"],
            occurred_at=v.get("occurred_at"),
        )
        return Response(shipment_out(_shipment(shipment_id), "en", staff=True))


class TrackingIn(serializers.Serializer):
    carrier = serializers.CharField(max_length=60, allow_blank=True)
    tracking_number = serializers.CharField(max_length=80)


class StaffShipmentTrackingView(ShipmentBase):
    @extend_schema(tags=TAG, summary="Set or correct the tracking number", request=TrackingIn, responses={200: dict})
    def post(self, request, shipment_id):
        data = TrackingIn(data=request.data)
        data.is_valid(raise_exception=True)
        shipments.set_tracking(_shipment(shipment_id), actor=request.user, **data.validated_data)
        return Response(shipment_out(_shipment(shipment_id), "en", staff=True))


class StaffShipmentSyncView(ShipmentBase):
    @extend_schema(tags=TAG, summary="Pull the carrier's tracking events now", request=None, responses={200: dict})
    def post(self, request, shipment_id):
        shipment = _shipment(shipment_id)
        new_events = shipments.sync_from_provider(shipment)
        return Response({"new_events": new_events, "shipment": shipment_out(_shipment(shipment_id), "en", staff=True)})
