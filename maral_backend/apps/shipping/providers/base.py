"""
The shipping provider contract. A provider answers three questions about a *method*: what does it cost / how long does it
take for this destination (`quote`), can it create the shipment and give a tracking number (`create_shipment`), and
where is the parcel now (`fetch_tracking`).

Two adapters ship: `table` (our own tariff tables: zones + rates, the normal way to run domestic and agreed-rate
international shipping) and a MOCK carrier for tests. Real carrier APIs (Iran Post, Tipax, DHL, Aramex ...) need a
contract, credentials and sandbox access; none is implemented or claimed (docs/shipping/SHIPPING_AND_INTERNATIONAL.md).
"""

import abc
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from apps.common.exceptions import BusinessRuleViolation


class ShippingUnavailable(BusinessRuleViolation):
    default_code = "shipping_method_unavailable"
    default_detail = "This delivery method is not available."


class ShippingProviderError(Exception):
    """The carrier could not be reached or answered nonsense; nothing may be assumed about the outcome."""


@dataclass(frozen=True)
class ShippingItem:
    quantity: int
    weight_grams: int | None = None  # None = unknown; never treated as zero
    category_slug: str = ""


@dataclass(frozen=True)
class Destination:
    country: str
    region: str = ""
    city: str = ""
    postal_code: str = ""

    @classmethod
    def from_address(cls, data: dict) -> "Destination":
        return cls(
            country=str(data.get("country", "")).upper(), region=str(data.get("province", "") or data.get("region", "")),
            city=str(data.get("city", "")), postal_code=str(data.get("postal_code", "")),
        )  # fmt: skip


@dataclass(frozen=True)
class ShippingRequest:
    destination: Destination
    merchandise_irr: Decimal
    items: tuple[ShippingItem, ...] | None = None  # None = not supplied (cart estimate)

    @property
    def total_weight_grams(self) -> int | None:
        """Total weight, or None when unknown (no items, or any line without a weight)."""
        if not self.items or any(i.weight_grams is None for i in self.items):
            return None
        return sum(i.weight_grams * i.quantity for i in self.items)


@dataclass
class ProviderRate:
    fee_irr: Decimal
    value_status: str  # confirmed | estimated
    is_free: bool = False
    zone_code: str = ""
    delivery_min_days: int | None = None
    delivery_max_days: int | None = None
    delivery_status: str = "unknown"  # unknown | estimated | confirmed
    service_name: str = ""


@dataclass
class ShipmentResult:
    tracking_number: str
    carrier_reference: str = ""
    tracking_url: str = ""
    estimated_delivery_at: datetime | None = None  # only if the carrier states it


@dataclass
class TrackingEvent:
    external_id: str
    status: str  # a ShipmentStatus value
    description: str = ""
    location: str = ""
    occurred_at: datetime | None = None


@dataclass
class TrackingResult:
    events: list[TrackingEvent] = field(default_factory=list)
    estimated_delivery_at: datetime | None = None


@dataclass(frozen=True)
class ShippingCapabilities:
    domestic: bool = True
    international: bool = False
    creates_shipments: bool = False
    supports_tracking_sync: bool = False
    is_mock: bool = False


class BaseShippingProvider(abc.ABC):
    key: str = ""
    capabilities: ShippingCapabilities

    @abc.abstractmethod
    def quote(self, method, request: ShippingRequest) -> ProviderRate | None:
        """None = this method does not serve the destination / basket. Raise ShippingUnavailable for a reason worth showing."""

    def create_shipment(self, method, shipment) -> ShipmentResult:
        raise ShippingProviderError("This provider cannot create shipments; enter the tracking number manually.")

    def fetch_tracking(self, shipment) -> TrackingResult:
        raise ShippingProviderError("This provider has no tracking API.")

    def cancel_shipment(self, shipment) -> None:  # noqa: B027
        """Best effort; default nothing to do."""
