from .models import ShipmentStatus

_LABELS = {
    "pending": {"fa": "در حال آماده‌سازی", "en": "Preparing"},
    "in_transit": {"fa": "در مسیر", "en": "In transit"},
    "out_for_delivery": {"fa": "در حال تحویل", "en": "Out for delivery"},
    "delivered": {"fa": "تحویل داده شد", "en": "Delivered"},
    "failed_attempt": {"fa": "تحویل ناموفق", "en": "Delivery attempt failed"},
    "returned": {"fa": "بازگشت به فرستنده", "en": "Returned to sender"},
    "cancelled": {"fa": "لغو شد", "en": "Cancelled"},
    "exception": {"fa": "مشکل در ارسال", "en": "Problem with the shipment"},
}


def status_label(status: str, lang: str = "fa") -> str:
    labels = _LABELS.get(status, {})
    return labels.get(lang) or labels.get("en") or status


def event_out(e, lang: str = "fa", *, staff: bool = False) -> dict:
    data = {
        "status": e.status,
        "status_label": status_label(e.status, lang),
        "description": e.description,
        "location": e.location,
        "occurred_at": e.occurred_at,
    }
    if staff:
        data.update(source=e.source, external_id=e.external_id, actor_id=e.actor_id)
    return data


def shipment_out(s, lang: str = "fa", *, staff: bool = False) -> dict:
    """Customers see carrier, tracking number/link, status, history and a carrier-confirmed estimate only if one exists."""
    data = {
        "id": str(s.public_id),
        "status": s.status,
        "status_label": status_label(s.status, lang),
        "carrier": s.carrier or None,
        "tracking_number": s.tracking_number or None,
        "tracking_url": s.tracking_url or None,
        "shipped_at": s.shipped_at,
        "delivered_at": s.delivered_at,
        "carrier_estimated_delivery_at": s.carrier_estimated_delivery_at,
        "events": [event_out(e, lang, staff=staff) for e in s.events.all()],
    }
    if staff:
        data.update(
            order_id=str(s.order.public_id),
            order_code=s.order.code,
            method_code=s.method_code,
            provider_key=s.provider_key,
            carrier_reference=s.carrier_reference,
            last_synced_at=s.last_synced_at,
            closed=s.status in ("delivered", "returned", "cancelled"),
        )
    return data


__all__ = ["ShipmentStatus", "event_out", "shipment_out", "status_label"]
