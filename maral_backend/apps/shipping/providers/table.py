"""Our own tariff tables: zones + rates configured by an administrator (nothing is seeded; an unpriced method is never zero)."""

import math
from decimal import Decimal

from ..geo import normalize_region
from ..models import EstimateStatus, ShippingZone
from .base import BaseShippingProvider, ProviderRate, ShippingCapabilities, ShippingRequest, ShippingUnavailable


def zone_matches(zone: ShippingZone, destination) -> bool:
    if destination.country.upper() not in [c.upper() for c in zone.countries]:
        return False
    if not zone.regions:
        return True
    wanted = {normalize_region(destination.country, r) for r in zone.regions}
    return bool(destination.region) and normalize_region(destination.country, destination.region) in wanted


def delivery_fields(min_days, max_days, method) -> tuple[int | None, int | None, str]:
    """A range only exists when an administrator entered one. Otherwise: unknown (never a made-up date)."""
    if min_days is None and max_days is None:
        min_days, max_days = method.delivery_min_days, method.delivery_max_days
    if min_days is None and max_days is None:
        return None, None, EstimateStatus.UNKNOWN
    status = method.delivery_estimate_status
    return min_days, max_days, (EstimateStatus.ESTIMATED if status == EstimateStatus.UNKNOWN else status)


class TableRateProvider(BaseShippingProvider):
    key = "table"
    capabilities = ShippingCapabilities(domestic=True, international=True)

    def quote(self, method, request: ShippingRequest) -> ProviderRate | None:
        rates = list(method.rates.filter(is_active=True, zone__is_active=True).select_related("zone"))
        if not rates:
            return self._legacy(method, request)
        zones = sorted(
            {r.zone for r in rates if zone_matches(r.zone, request.destination)},
            key=lambda z: (z.priority, 0 if z.regions else 1, z.pk),
        )
        if not zones:
            return None
        zone = zones[0]
        merchandise = request.merchandise_irr
        rate = next(
            (
                r
                for r in rates
                if r.zone_id == zone.pk
                and r.min_subtotal_irr <= merchandise
                and (r.max_subtotal_irr is None or merchandise < r.max_subtotal_irr)
            ),
            None,
        )
        if rate is None:
            return None
        threshold = rate.free_over_irr if rate.free_over_irr is not None else method.free_over_irr
        low, high, status = delivery_fields(rate.delivery_min_days, rate.delivery_max_days, method)
        common = dict(
            value_status=rate.value_status,
            zone_code=zone.code,
            delivery_min_days=low,
            delivery_max_days=high,
            delivery_status=status,
        )
        if threshold is not None and merchandise >= threshold:
            return ProviderRate(fee_irr=Decimal(0), is_free=True, **common)
        fee = rate.base_fee_irr
        if rate.per_kg_irr > 0:
            weight = request.total_weight_grams
            if weight is None:
                raise ShippingUnavailable(
                    "The parcel weight is needed to price this method.", code="shipping_weight_required"
                )
            extra = max(weight - rate.included_weight_grams, 0)
            fee += Decimal(math.ceil(extra / 1000)) * rate.per_kg_irr
        return ProviderRate(fee_irr=fee, **common)

    @staticmethod
    def _legacy(method, request: ShippingRequest) -> ProviderRate | None:
        """Phase 7 methods: a flat fee for the countries listed on the method."""
        if request.destination.country.upper() not in [c.upper() for c in method.countries]:
            return None
        free = method.free_over_irr is not None and request.merchandise_irr >= method.free_over_irr
        low, high, status = delivery_fields(None, None, method)
        return ProviderRate(
            fee_irr=Decimal(0) if free else method.fee_irr, value_status=method.value_status, is_free=free,
            delivery_min_days=low, delivery_max_days=high, delivery_status=status,
        )  # fmt: skip
