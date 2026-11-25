"""Customer shipping API: where we deliver, what the address needs, which methods / what they cost for a destination."""

from decimal import Decimal

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.permissions import CsrfRequired
from apps.accounts.views.base import NoStoreMixin
from apps.cart.services import identity
from apps.catalog.context import get_context, money
from apps.common.exceptions import BusinessRuleViolation

from . import addresses
from .geo import IRAN_PROVINCES
from .models import CountryShippingPolicy, ShippingMethod, ShippingZone
from .services import international, methods


class ShippingBase(NoStoreMixin, APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "cart"


class DestinationIn(serializers.Serializer):
    country = serializers.RegexField(r"^[A-Za-z]{2}$")
    region = serializers.CharField(max_length=80, required=False, allow_blank=True, default="")
    city = serializers.CharField(max_length=80, required=False, allow_blank=True, default="")
    postal_code = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")


def _basket(request) -> tuple[Decimal, list | None]:
    """The caller's own cart, priced server-side. No cart (or nothing priced) = basket unknown (never an invented total)."""
    cart = identity.find_active_cart(request)
    if cart is None:
        return Decimal(0), None
    rows = list(cart.items.select_related("variant__product__category", "variant__price"))
    if not rows:
        return Decimal(0), None
    subtotal = Decimal(0)
    for row in rows:
        price = getattr(row.variant, "price", None)
        if price is not None:
            subtotal += price.final_irr * row.quantity
    return subtotal, methods.items_from_cart(rows)


def build_estimate(request, data: dict) -> dict:
    ctx = get_context(request)
    country = data["country"].upper()
    merchandise, items = _basket(request)
    result = methods.resolve(
        country,
        merchandise,
        region=data.get("region", ""),
        city=data.get("city", ""),
        postal_code=data.get("postal_code", ""),
        items=items,
    )
    cur = ctx.currency
    rows = [
        {
            "code": o.code, "label": o.label(ctx.lang), "kind": o.kind, "carrier": o.carrier_code or None,
            "description": o.descriptions.get(ctx.lang) or o.descriptions.get("en") or "",
            "fee": money(o.fee_irr, cur), "is_free": o.is_free, "fee_status": o.value_status, "delivery": o.delivery(),
        }
        for o in result.options
    ]  # fmt: skip
    has_range = any(o.delivery()["status"] != "unknown" for o in result.options)
    customs = international.customs_info(country, lang=ctx.lang)  # a country-level estimate never carries an amount
    return {
        "destination": {"country": country, "region": data.get("region", ""), "city": data.get("city", "")},
        "currency": cur,
        "basket_known": items is not None,
        "supported": bool(rows),
        "methods": rows,
        "unavailable": [{"method": u["method"], "reason": u["reason"]} for u in result.unavailable],
        "customs": {k: v for k, v in customs.items() if k != "amount_irr"},
        "policy_notes": international.policy_notes(country, ctx.lang),
        "address_requirements": addresses.requirements_for(country).as_dict(),
        "disclaimers": [
            *(["delivery_range_is_an_estimate"] if has_range else []),
            *(["fee_may_be_estimated"] if any(o.value_status == "estimated" for o in result.options) else []),
            "customs_not_included_unless_stated",
        ],
    }


class ShippingMethodsView(ShippingBase):
    @extend_schema(
        tags=["shipping"], summary="Delivery methods and fees for a destination (uses your cart when there is one)",
        parameters=[OpenApiParameter(n, str, required=(n == "country")) for n in ("country", "region", "city", "postal_code")], responses={200: dict},
    )  # fmt: skip
    def get(self, request):
        data = DestinationIn(data=request.query_params)
        data.is_valid(raise_exception=True)
        return Response(build_estimate(request, data.validated_data))


class ShippingEstimateView(ShippingBase):
    permission_classes = [AllowAny, CsrfRequired]
    parser_classes = [JSONParser]

    @extend_schema(
        tags=["shipping"],
        summary="Shipping estimate for a destination: methods, fees, delivery ranges, customs status",
        request=DestinationIn,
        responses={200: dict},
    )
    def post(self, request):
        data = DestinationIn(data=request.data)
        data.is_valid(raise_exception=True)
        return Response(build_estimate(request, data.validated_data))


class ShippingCountriesView(ShippingBase):
    @extend_schema(
        tags=["shipping"],
        summary="Countries we can deliver to (from configured zones / methods; blocked countries are excluded)",
        responses={200: dict},
    )
    def get(self, request):
        ctx = get_context(request)
        codes: set[str] = set()
        for zone in ShippingZone.objects.filter(
            is_active=True, rates__is_active=True, rates__method__is_active=True
        ).distinct():
            codes |= {c.upper() for c in zone.countries}
        for method in ShippingMethod.objects.filter(is_active=True, rates__isnull=True):
            codes |= {c.upper() for c in method.countries}
        policies = {p.country: p for p in CountryShippingPolicy.objects.filter(is_active=True)}
        out = []
        for code in sorted(codes):
            policy = policies.get(code)
            if policy is not None and not policy.is_shippable:
                continue
            names = policy.names if policy else {}
            out.append(
                {
                    "code": code,
                    "name": names.get(ctx.lang) or names.get("en") or code,
                    "customs_mode": policy.customs_mode if policy else "unknown",
                }
            )
        return Response({"countries": out})


class AddressRequirementsView(ShippingBase):
    @extend_schema(
        tags=["shipping"],
        summary="Address fields required for a country (configurable per country) and Iranian provinces",
        responses={200: dict},
    )
    def get(self, request):
        country = str(request.query_params.get("country", "")).upper()
        if len(country) != 2 or not country.isalpha():
            raise BusinessRuleViolation("country is required (ISO 3166-1 alpha-2).", code="invalid_country")
        body = addresses.requirements_for(country).as_dict()
        if country == "IR":
            body["provinces"] = [{"key": key, "fa": fa} for key, fa, _ in IRAN_PROVINCES]
        return Response(body)
