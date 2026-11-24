from dataclasses import dataclass
from decimal import Decimal

from apps.common.exceptions import BusinessRuleViolation

from ..models import ShippingMethod


@dataclass(frozen=True)
class ShippingQuote:
    code: str
    names: dict
    fee_irr: Decimal
    is_free: bool
    value_status: str

    def label(self, lang="fa") -> str:
        return self.names.get(lang) or self.names.get("fa") or self.names.get("en") or self.code


def _quote(method: ShippingMethod, merchandise_irr: Decimal) -> ShippingQuote:
    free = method.free_over_irr is not None and merchandise_irr >= method.free_over_irr
    return ShippingQuote(
        code=method.code,
        names=method.names,
        fee_irr=Decimal(0) if free else method.fee_irr,
        is_free=free,
        value_status=method.value_status,
    )


def options(country: str, merchandise_irr: Decimal) -> list[ShippingQuote]:
    methods = ShippingMethod.objects.filter(is_active=True).order_by("sort_order", "id")
    return [_quote(m, merchandise_irr) for m in methods if country.upper() in [c.upper() for c in m.countries]]


def quote(code: str, country: str, merchandise_irr: Decimal) -> ShippingQuote:
    for option in options(country, merchandise_irr):
        if option.code == code:
            return option
    raise BusinessRuleViolation("This delivery method is not available for the address.", code="shipping_method_unavailable")


def estimate(merchandise_irr: Decimal, country: str = "IR") -> ShippingQuote | None:
    """Cart estimate: the first (default) method for the default country. None = nothing configured (never zero)."""
    available = options(country, merchandise_irr)
    return available[0] if available else None


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
