"""Public currency endpoints: which currencies exist and the customer-facing (display) rates. No source names, no health."""

from decimal import Decimal

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from ..errors import RateError
from ..models import Currency, RateRoute
from ..services.resolver import resolve_rate

TAG = ["currencies"]


def plain(value: Decimal) -> str:
    """Decimal as a plain string without exponent or trailing zeros (money is always serialised as a string)."""
    text = format(value.normalize(), "f")
    return text


class PublicView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def dispatch(self, request, *args, **kwargs):
        self._url_version = kwargs.pop("version", None)
        return super().dispatch(request, *args, **kwargs)

    def determine_version(self, request, *args, **kwargs):
        if getattr(self, "_url_version", None) is not None:
            kwargs["version"] = self._url_version
        return super().determine_version(request, *args, **kwargs)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if request.method == "GET" and response.status_code == 200:
            response["Cache-Control"] = "public, max-age=60"
        return response


class CurrencyListView(PublicView):
    @extend_schema(tags=TAG, summary="Supported currencies", responses={200: dict})
    def get(self, request):
        rows = Currency.objects.filter(is_active=True).order_by("sort_order", "code")
        return Response(
            {
                "results": [
                    {
                        "code": c.code,
                        "name": c.name,
                        "symbol": c.symbol,
                        "decimal_places": c.decimal_places,
                        "is_display": c.is_display,
                        "is_source": c.is_source,
                        "unit_of": {"currency": c.base_currency_id, "factor": plain(c.base_factor)}
                        if c.base_currency_id
                        else None,
                    }
                    for c in rows
                ]
            }
        )


class ExchangeRateListView(PublicView):
    @extend_schema(
        tags=TAG, summary="Current display exchange rates (IRR per 1 unit of each currency)", responses={200: dict}
    )
    def get(self, request):
        toman = Currency.objects.filter(code="IRT").first()
        factor = toman.base_factor if toman else Decimal(10)
        rates, unavailable = [], []
        for route in RateRoute.objects.filter(
            purpose="display", quote_id="IRR", is_enabled=True, base__is_active=True, base__is_display=True
        ):
            try:
                r = resolve_rate(route.base_id, "IRR", "display")
            except RateError as exc:
                unavailable.append({"currency": route.base_id, "reason": exc.code})
                continue
            rates.append(
                {
                    "currency": route.base_id,
                    "irr_per_unit": plain(r.rate),
                    "irt_per_unit": plain(r.rate / factor),
                    "as_of": r.rate_at.isoformat() if r.rate_at else None,
                    "valid_until": r.usable_until.isoformat() if r.usable_until else None,
                    "is_stale": r.is_stale,
                    "indicative": True,
                }
            )
        return Response(
            {
                "base_unit": "IRR",
                "rates": rates,
                "unavailable": unavailable,
                "note": "Prices are charged in IRR; other currencies are indicative.",
            }
        )
