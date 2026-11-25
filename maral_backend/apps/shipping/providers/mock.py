"""MOCK carrier for tests / development (settings.SHIPPING_ALLOW_MOCK_CARRIER). Ships nothing. Remote state in the cache."""

import uuid
from datetime import datetime
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone

from ..models import ShipmentStatus
from .base import (
    BaseShippingProvider,
    ProviderRate,
    ShipmentResult,
    ShippingCapabilities,
    ShippingProviderError,
    ShippingRequest,
    TrackingEvent,
    TrackingResult,
)


class MockCarrierRemote:
    """What the pretend carrier knows: a list of scan events per tracking number."""

    @staticmethod
    def _k(tracking: str) -> str:
        return f"mockship:{tracking}"

    @classmethod
    def scan(cls, tracking: str, status: str, description: str = "", location: str = "", when=None) -> dict:
        events = cache.get(cls._k(tracking), [])
        event = {
            "id": f"scan-{len(events) + 1}",
            "status": status,
            "description": description,
            "location": location,
            "at": (when or timezone.now()).isoformat(),
        }
        cache.set(cls._k(tracking), [*events, event], 86400)
        return event

    @classmethod
    def events(cls, tracking: str) -> list[dict]:
        return cache.get(cls._k(tracking), [])

    @staticmethod
    def set_outage(on: bool = True) -> None:
        cache.set("mockship:outage", on, 86400)

    @staticmethod
    def outage() -> bool:
        return bool(cache.get("mockship:outage"))


class MockCarrierProvider(BaseShippingProvider):
    """Quote from `method.config` = {"countries": ["DE"], "fee_irr": "9000000", "min_days": 5, "max_days": 9}."""

    key = "mock_carrier"
    capabilities = ShippingCapabilities(
        domestic=True, international=True, creates_shipments=True, supports_tracking_sync=True, is_mock=True
    )

    def quote(self, method, request: ShippingRequest) -> ProviderRate | None:
        cfg = method.config or {}
        if (
            request.destination.country.upper() not in [c.upper() for c in cfg.get("countries", [])]
            or "fee_irr" not in cfg
        ):
            return None
        low, high = cfg.get("min_days"), cfg.get("max_days")
        return ProviderRate(
            fee_irr=Decimal(str(cfg["fee_irr"])), value_status="estimated", delivery_min_days=low, delivery_max_days=high,
            delivery_status="estimated" if low is not None or high is not None else "unknown", zone_code="mock",
        )  # fmt: skip

    def create_shipment(self, method, shipment) -> ShipmentResult:
        if MockCarrierRemote.outage():
            raise ShippingProviderError("mock carrier outage")
        tracking = f"MOCK{uuid.uuid4().hex[:12].upper()}"
        MockCarrierRemote.scan(tracking, ShipmentStatus.PENDING, "Label created")
        return ShipmentResult(
            tracking_number=tracking, carrier_reference=f"mock-{shipment.public_id.hex[:8]}", estimated_delivery_at=None
        )

    def fetch_tracking(self, shipment) -> TrackingResult:
        if MockCarrierRemote.outage():
            raise ShippingProviderError("mock carrier outage")
        if not shipment.tracking_number:
            raise ShippingProviderError("no tracking number")
        events = [
            TrackingEvent(
                external_id=e["id"],
                status=e["status"],
                description=e["description"],
                location=e["location"],
                occurred_at=datetime.fromisoformat(e["at"]),
            )
            for e in MockCarrierRemote.events(shipment.tracking_number)
        ]
        return TrackingResult(events=events, estimated_delivery_at=None)


__all__ = ["MockCarrierProvider", "MockCarrierRemote"]
