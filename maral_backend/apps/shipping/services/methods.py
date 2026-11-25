"""
Shipping options and prices for a destination.

`resolve()` is the single place that decides which delivery methods exist for an address and what they cost. Providers
price (our `table` tariffs or a carrier adapter); this module adds the rules that apply to every provider: country policy
(blocked destinations, restricted categories), weight limits, and the rule that an unknown value stays unknown (a missing
weight or tariff makes a method *unavailable with a reason*, never free or zero).

Phase 7 call signatures (`options`, `quote`, `estimate`, `allocate`) keep working: extra keyword arguments are optional.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from apps.common.exceptions import BusinessRuleViolation

from ..models import CountryShippingPolicy, MethodKind, ShippingMethod
from ..providers import registry
from ..providers.base import Destination, ShippingItem, ShippingProviderError, ShippingRequest, ShippingUnavailable


@dataclass(frozen=True)
class ShippingQuote:
    code: str
    names: dict
    fee_irr: Decimal
    is_free: bool
    value_status: str
    kind: str = MethodKind.DOMESTIC
    zone_code: str = ""
    carrier_code: str = ""
    provider_key: str = "table"
    requires_tracking: bool = False
    delivery_min_days: int | None = None
    delivery_max_days: int | None = None
    delivery_status: str = "unknown"
    descriptions: dict = field(default_factory=dict)

    def label(self, lang="fa") -> str:
        return self.names.get(lang) or self.names.get("fa") or self.names.get("en") or self.code

    def delivery(self) -> dict:
        """A day range only when one is configured. Never an exact date; unknown is reported as unknown."""
        if self.delivery_min_days is None and self.delivery_max_days is None:
            return {"status": "unknown", "min_days": None, "max_days": None, "unit": "business_days"}
        return {
            "status": self.delivery_status,
            "min_days": self.delivery_min_days,
            "max_days": self.delivery_max_days,
            "unit": "business_days",
        }

    def snapshot(self) -> dict:
        return {
            "method": self.code, "kind": self.kind, "zone": self.zone_code, "carrier": self.carrier_code, "provider": self.provider_key,
            "requires_tracking": self.requires_tracking, "delivery": self.delivery(), "fee_status": self.value_status, "is_free": self.is_free,
        }  # fmt: skip


@dataclass
class Resolution:
    options: list[ShippingQuote] = field(default_factory=list)
    unavailable: list[dict] = field(default_factory=list)  # {"method": code | "*", "reason": ...}
    destination_served: bool = False  # at least one method priced this destination


def policy_for(country: str) -> CountryShippingPolicy | None:
    return CountryShippingPolicy.objects.filter(country=(country or "").upper(), is_active=True).first()


def resolve(
    country: str,
    merchandise_irr: Decimal,
    *,
    region: str = "",
    city: str = "",
    postal_code: str = "",
    items=None,
    only: str | None = None,
) -> Resolution:
    destination = Destination(
        country=(country or "").upper(), region=region or "", city=city or "", postal_code=postal_code or ""
    )
    request = ShippingRequest(destination, Decimal(merchandise_irr), tuple(items) if items is not None else None)
    result = Resolution()
    policy = policy_for(destination.country)
    if policy is not None and not policy.is_shippable:
        result.unavailable.append({"method": "*", "reason": "country_not_shippable"})
        return result
    if policy is not None and policy.restricted_category_slugs and items:
        blocked = sorted(
            {i.category_slug for i in items if i.category_slug and i.category_slug in policy.restricted_category_slugs}
        )
        if blocked:
            result.unavailable.append({"method": "*", "reason": "restricted_items", "detail": blocked})
            return result
    methods = ShippingMethod.objects.filter(is_active=True).order_by("sort_order", "id")
    if only:
        methods = methods.filter(code=only)
    for method in methods:
        adapter = registry.get(method.provider_key)
        if adapter is None:
            result.unavailable.append({"method": method.code, "reason": "provider_unavailable"})
            continue
        if method.max_weight_grams and request.items is not None:
            weight = request.total_weight_grams
            if weight is None:
                result.unavailable.append({"method": method.code, "reason": "weight_required"})
                continue
            if weight > method.max_weight_grams:
                result.unavailable.append({"method": method.code, "reason": "overweight"})
                continue
        try:
            rate = adapter.quote(method, request)
        except ShippingUnavailable as exc:
            result.unavailable.append({"method": method.code, "reason": str(exc.get_codes()).removeprefix("shipping_")})
            continue
        except ShippingProviderError:
            result.unavailable.append({"method": method.code, "reason": "provider_error"})
            continue
        if rate is None:
            continue
        result.destination_served = True
        result.options.append(
            ShippingQuote(
                code=method.code, names=method.names, fee_irr=rate.fee_irr, is_free=rate.is_free, value_status=rate.value_status,
                kind=method.kind, zone_code=rate.zone_code, carrier_code=method.carrier_code, provider_key=method.provider_key,
                requires_tracking=method.requires_tracking, delivery_min_days=rate.delivery_min_days, delivery_max_days=rate.delivery_max_days,
                delivery_status=rate.delivery_status, descriptions=method.descriptions,
            )
        )  # fmt: skip
    return result


def options(country: str, merchandise_irr: Decimal, **kwargs) -> list[ShippingQuote]:
    return resolve(country, merchandise_irr, **kwargs).options


_REASON_CODES = {
    "weight_required": "shipping_weight_required",
    "overweight": "shipping_overweight",
    "restricted_items": "shipping_restricted_items",
    "country_not_shippable": "shipping_country_not_shippable",
    "provider_error": "shipping_provider_error",
}


def quote(code: str, country: str, merchandise_irr: Decimal, **kwargs) -> ShippingQuote:
    result = resolve(country, merchandise_irr, only=code, **kwargs)
    if result.options:
        return result.options[0]
    for row in result.unavailable:
        if row["method"] in (code, "*") and row["reason"] in _REASON_CODES:
            raise ShippingUnavailable(
                "This delivery method cannot be used for this order.", code=_REASON_CODES[row["reason"]]
            )
    reasons = (
        {"destination": ["unsupported_destination"]}
        if not resolve(country, merchandise_irr, **kwargs).destination_served
        else None
    )
    raise BusinessRuleViolation(
        "This delivery method is not available for the address.", code="shipping_method_unavailable", fields=reasons
    )


def has_subtotal_tiers(code: str) -> bool:
    """True when the method's price depends on the basket value (so a destination check at subtotal 0 proves nothing)."""
    return ShippingMethod.objects.filter(code=code, rates__min_subtotal_irr__gt=0).exists()


def estimate(merchandise_irr: Decimal, country: str = "IR") -> ShippingQuote | None:
    """Cart estimate: the first (default) method for the default country. None = nothing configured (never zero)."""
    available = options(country, merchandise_irr)
    return available[0] if available else None


def items_from_cart(cart_items) -> list[ShippingItem]:
    return [
        ShippingItem(
            quantity=i.quantity,
            weight_grams=i.variant.weight_grams,
            category_slug=getattr(getattr(i.variant.product, "category", None), "slug", "") or "",
        )
        for i in cart_items
    ]


def allocate(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Split a whole-rial amount across parts proportionally; the remainder goes to the largest parts (sums exactly)."""
    if not weights:
        return []
    weight_sum = sum(weights, Decimal(0))
    if weight_sum == 0:
        shares = [Decimal(0)] * len(weights)
        shares[0] = total
        return shares
    shares = [(total * w / weight_sum).to_integral_value(rounding="ROUND_DOWN") for w in weights]
    remainder = int(total - sum(shares, Decimal(0)))
    for index in sorted(range(len(weights)), key=lambda i: weights[i], reverse=True)[:remainder]:
        shares[index] += 1
    return shares
