"""
Customer-facing pricing API. Responses never contain profit, margin, internal cost or rule names; when a price cannot
be given the customer sees a generic `price_unavailable` error (the reasons are for staff, not for the shop window).
"""

import hashlib

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.permissions import CsrfRequired
from apps.accounts.views.base import NoStoreMixin
from apps.catalog.models import Product, ProductVariant
from apps.common.exceptions import BusinessRuleViolation
from apps.currencies.models import Currency
from apps.currencies.views.public import plain

from ..errors import PriceUnavailable
from ..models import PriceQuote, QuoteStatus
from ..services import inputs, quotes
from ..services.engine import calculate
from ..services.presentation import customer_breakdown
from ..services.rulesets import active_ruleset

TAG = ["pricing"]
GUEST_HEADER = "X-Guest-Token"


class PricingView(NoStoreMixin, APIView):
    permission_classes = [AllowAny, CsrfRequired]
    parser_classes = [JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "price_calculate"


def money(value, currency) -> str:
    return plain(value)


def _variant(public_id):
    variant = (
        ProductVariant.objects.select_related("product__category", "product__brand", "source")
        .filter(public_id=public_id, is_active=True, product__status="active")
        .first()
    )
    if variant is None:
        raise NotFound()
    return variant


def _currency(code: str) -> Currency:
    currency = Currency.objects.filter(code=code, is_active=True, is_display=True).first()
    if currency is None:
        raise BusinessRuleViolation("Unsupported display currency.", code="unsupported_currency")
    return currency


def calc_out(calc, rule_set, currency) -> dict:
    rate = calc.rate
    return {
        "currency": currency,
        "unit_price": money(calc.unit_display, currency),
        "unit_separate_charges": money(
            calc.unit_separate_irr / (Currency.objects.get(code=currency).base_factor), currency
        )
        if currency in ("IRT", "IRR")
        else None,
        "unit_price_irr": plain(calc.unit_price_irr),
        "unit_separate_irr": plain(calc.unit_separate_irr),
        "has_estimated_costs": calc.has_estimated,
        "rate": {"pair": f"{rate.base}/{rate.quote}", "as_of": rate.rate_at, "valid_until": rate.usable_until}
        if rate and rate.rate_at
        else None,
        "indicative": currency not in ("IRT", "IRR"),
        "breakdown": customer_breakdown(calc, rule_set.customer_breakdown),
    }


class CalculateIn(serializers.Serializer):
    variant_id = serializers.UUIDField()
    currency = serializers.CharField(max_length=3, required=False, default="IRT")
    destination_country = serializers.CharField(max_length=2, required=False, allow_blank=True, default="")
    shipping_method = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")


class CalculateView(PricingView):
    @extend_schema(
        tags=TAG,
        summary="Live price for a variant (not a quote; nothing is saved)",
        request=CalculateIn,
        responses={200: dict},
    )
    def post(self, request):
        data = CalculateIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        _currency(v["currency"])
        variant = _variant(v["variant_id"])
        rule_set = active_ruleset()
        inp, reason = inputs.build_input(
            variant,
            rule_set,
            destination_country=v["destination_country"],
            shipping_method=v["shipping_method"],
            presentation_currency=v["currency"],
        )
        if inp is None:
            raise PriceUnavailable("A price is not available for this item right now.", blockers=[{"code": reason}])
        calc = calculate(inp, rule_set)
        if not calc.quotable:
            raise PriceUnavailable(
                "A price is not available for this item right now.", blockers=[b.as_dict() for b in calc.blockers]
            )
        return Response(
            {
                "variant_id": str(variant.public_id),
                **calc_out(calc, rule_set, v["currency"]),
                "calculated_at": timezone.now(),
            }
        )


class ProductPriceView(PricingView):
    @extend_schema(tags=TAG, summary="Stored price details for every variant of a product", responses={200: dict})
    def get(self, request, id):
        product = Product.objects.filter(public_id=id, status="active").first()
        if product is None:
            raise NotFound()
        currency = request.query_params.get("currency", "IRT")
        cur = _currency(currency)
        out = []
        for variant in product.variants.filter(is_active=True).select_related("price").order_by("sort_order", "id"):
            price = getattr(variant, "price", None)
            if price is None:
                out.append({"variant_id": str(variant.public_id), "sku": variant.sku, "available": False})
                continue
            factor = cur.base_factor if cur.base_currency_id == "IRR" else None
            if currency == "IRR":
                factor = 1
            if factor is None:
                out.append(
                    {
                        "variant_id": str(variant.public_id),
                        "sku": variant.sku,
                        "available": False,
                        "reason": "currency_requires_quote",
                    }
                )
                continue
            out.append(
                {
                    "variant_id": str(variant.public_id),
                    "sku": variant.sku,
                    "available": True,
                    "currency": currency,
                    "final_price": plain(price.final_irr / factor),
                    "list_price": plain(price.list_irr / factor),
                    "discount_percent": plain(price.discount_percent),
                    "computed_at": price.computed_at,
                    "origin": price.origin,
                    "breakdown": price.breakdown,
                }
            )
        return Response(
            {
                "product_id": str(product.public_id),
                "currency": currency,
                "variants": out,
                "note": "Stored storefront prices. They can change; request a quote to lock a price.",
            }
        )


# ------------------------------------------------------------------------------------------------ quotes
class QuoteItemIn(serializers.Serializer):
    variant_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, max_value=quotes.MAX_QTY)


class QuoteIn(serializers.Serializer):
    items = QuoteItemIn(many=True, allow_empty=False, max_length=quotes.MAX_ITEMS)
    currency = serializers.CharField(max_length=3, required=False, default="IRT")
    destination_country = serializers.CharField(max_length=2, required=False, allow_blank=True, default="")
    shipping_method = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")


def quote_out(quote: PriceQuote, *, full: bool = False) -> dict:
    status = QuoteStatus.EXPIRED if quote.is_expired else quote.status
    cur = quote.display_currency_id
    items = []
    for item in quote.items.all():
        row = {
            "variant_id": str(item.variant.public_id),
            "sku": item.sku,
            "name": item.product_name,
            "quantity": item.quantity,
            "unit_price": plain(item.unit_display),
            "line_price": plain(item.line_display),
            "line_price_irr": plain(item.line_price_irr),
            "line_separate_irr": plain(item.line_separate_irr),
            "has_estimated_costs": item.has_estimated_costs,
            "rate": {
                "pair": f"{item.original_currency}/IRR",
                "applied": plain(item.rate_applied),
                "as_of": item.rate_at,
            },
        }
        if full:
            row["calculation"] = item.breakdown
            row["unit_realized_profit_irr"] = plain(item.unit_realized_profit_irr)
            row["components"] = [
                {
                    "kind": c.kind,
                    "code": c.code,
                    "name": c.name,
                    "amount_irr": plain(c.amount_irr),
                    "in_price": c.in_price,
                    "charge_mode": c.charge_mode,
                    "value_status": c.value_status,
                }
                for c in item.components.all()
            ]
        items.append(row)
    out = {
        "id": str(quote.public_id),
        "status": status,
        "currency": cur,
        "created_at": quote.created_at,
        "expires_at": quote.expires_at,
        "items": items,
        "subtotal_irr": plain(quote.subtotal_irr),
        "separate_charges_irr": plain(quote.separate_irr),
        "total_irr": plain(quote.total_irr),
        "total": plain(quote.total_display),
        "indicative": cur not in ("IRT", "IRR"),
        "has_estimated_costs": quote.has_estimated_costs,
        "rule_version": quote.rule_version,
    }
    if quote.display_rate is not None:
        out["display_rate"] = {"irr_per_unit": plain(quote.display_rate), "as_of": quote.display_rate_at}
    if full:
        out.update(
            calculation_hash=quote.calculation_hash,
            locked_reference=quote.locked_reference,
            locked_at=quote.locked_at,
            warnings=quote.warnings,
            destination_country=quote.destination_country,
        )
    return out


def guest_token(request) -> str:
    return request.headers.get(GUEST_HEADER, "").strip()


def _owned_quote(request, public_id) -> PriceQuote:
    quote = (
        PriceQuote.objects.select_related("display_currency")
        .prefetch_related("items__variant", "items__components")
        .filter(public_id=public_id)
        .first()
    )
    if quote is None:
        raise NotFound()
    user = request.user if request.user.is_authenticated else None
    token = guest_token(request)
    if user is not None and quote.user_id == user.pk:
        return quote
    if token and quote.guest_token_hash and quote.guest_token_hash == hashlib.sha256(token.encode()).hexdigest():
        return quote
    raise NotFound()  # someone else's quote looks exactly like a missing one


class QuoteCreateView(PricingView):
    throttle_scope = "price_quote"

    @extend_schema(
        tags=TAG, summary="Create a price quote (a price held until it expires)", request=QuoteIn, responses={201: dict}
    )
    def post(self, request):
        data = QuoteIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        token = guest_token(request)
        if not request.user.is_authenticated and len(token) < 16:
            raise BusinessRuleViolation("Sign in or send a guest token to request a quote.", code="owner_required")
        pairs = []
        for item in v["items"]:
            pairs.append((_variant(item["variant_id"]), item["quantity"]))
        quote = quotes.create_quote(
            items=pairs,
            currency=v["currency"],
            user=request.user,
            guest_token=token,
            destination_country=v["destination_country"],
            shipping_method=v["shipping_method"],
        )
        return Response(quote_out(_owned_quote(request, quote.public_id)), status=201)


class QuoteDetailView(PricingView):
    @extend_schema(tags=TAG, summary="Read your quote", responses={200: dict})
    def get(self, request, id):
        return Response(quote_out(_owned_quote(request, id)))


class QuoteExpireView(PricingView):
    @extend_schema(tags=TAG, summary="Expire (cancel) your open quote", request=None, responses={200: dict})
    def post(self, request, id):
        quote = _owned_quote(request, id)
        quotes.expire_quote(quote.pk)
        return Response(quote_out(_owned_quote(request, id)))
