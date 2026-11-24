"""
Customer-visible status labels. PROVISIONAL wording: the owner has not approved the list yet ([Q-orders],
docs/orders/PHASE7_DECISIONS.md). Internal reasons / staff identities are never part of these.
"""

LABELS = {
    "PENDING_PAYMENT": {"fa": "در انتظار پرداخت", "en": "Awaiting payment"},
    "PAID": {"fa": "پرداخت شده", "en": "Paid"},
    "PROCESSING": {"fa": "در حال پردازش", "en": "Processing"},
    "PURCHASE_PENDING": {"fa": "در انتظار خرید از فروشگاه", "en": "Waiting to be purchased"},
    "PURCHASED": {"fa": "خریداری شده", "en": "Purchased"},
    "IN_TRANSIT": {"fa": "در حال ارسال از مبدأ", "en": "In international transit"},
    "CUSTOMS_PROCESSING": {"fa": "در حال ترخیص", "en": "Customs processing"},
    "READY_TO_SHIP": {"fa": "آماده ارسال", "en": "Ready to ship"},
    "SHIPPED": {"fa": "ارسال شده", "en": "Shipped"},
    "DELIVERED": {"fa": "تحویل شده", "en": "Delivered"},
    "CANCELLED": {"fa": "لغو شده", "en": "Cancelled"},
    "REFUND_PENDING": {"fa": "در انتظار بازپرداخت", "en": "Refund pending"},
    "PARTIALLY_REFUNDED": {"fa": "بازپرداخت جزئی", "en": "Partially refunded"},
    "REFUNDED": {"fa": "بازپرداخت شده", "en": "Refunded"},
}


def label(status: str, lang: str = "fa") -> str:
    entry = LABELS.get(status, {})
    return entry.get(lang) or entry.get("en") or status
