"""
Checkout: validate -> authoritative quote -> totals. Steps 1-8 of the Phase 7 brief.

1 validate cart  2 validate availability  3 validate variants  4 validate address  5 shipping  6 customs/taxes estimate
7 create the price quote (server-side, expires)  8 order summary.  (9 order, 10 payment: services.placing / payments)

Nothing the client sends is a price. The only client inputs are: which saved address, which delivery method, which
display currency.
"""

import hashlib
from decimal import Decimal

import structlog
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound

from apps.accounts.models import Address
from apps.cart.models import Cart, CartStatus
from apps.cart.services import availability
from apps.catalog.context import money
from apps.common.exceptions import BusinessRuleViolation
from apps.currencies.models import Currency
from apps.pricing.models import PriceQuoteComponent
from apps.pricing.services import quotes
from apps.shipping import addresses
from apps.shipping.services import international
from apps.shipping.services import methods as shipping

from ..models import Checkout, CheckoutStatus

logger = structlog.get_logger("maral.orders")


class CartInvalid(BusinessRuleViolation):
    default_code = "cart_invalid"
    default_detail = "Some items in your cart cannot be ordered."


def fingerprint(pairs) -> str:
    """pairs = [(variant_id, quantity)] -> stable hash; detects any change of what is being bought."""
    text = ",".join(f"{v}:{q}" for v, q in sorted(pairs))
    return hashlib.sha256(text.encode()).hexdigest()


def address_snapshot(address: Address) -> dict:
    return {
        "recipient_name": address.recipient_name,
        "phone": address.phone,
        "country": address.country,
        "province": address.province,
        "city": address.city,
        "district": address.district,
        "address_line1": address.address_line1,
        "address_line2": address.address_line2,
        "postal_code": address.postal_code,
        "delivery_instructions": address.delivery_instructions,
    }


def validate_address(address: Address) -> None:
    """Country-aware delivery address rules (configurable per country; Persian digits and letters are normalised)."""
    addresses.validate({f: getattr(address, f, "") for f in addresses.KNOWN_FIELDS})


def _customs_totals(quote) -> tuple[Decimal, Decimal]:
    """(estimated, confirmed) customs from the quote's components: estimates and confirmed values are never mixed."""
    rows = PriceQuoteComponent.objects.filter(item__quote=quote, kind="customs").select_related("item")
    estimated = sum((c.amount_irr * c.item.quantity for c in rows if c.value_status == "estimated"), Decimal(0))
    confirmed = sum((c.amount_irr * c.item.quantity for c in rows if c.value_status != "estimated"), Decimal(0))
    return estimated, confirmed


def validate_cart(cart: Cart):
    """Steps 1-3. Returns [(CartItem)] or raises CartInvalid listing every problem line."""
    items = list(cart.items.select_related("variant__product"))
    if not items:
        raise BusinessRuleViolation("Your cart is empty.", code="cart_empty")
    issues = availability.assess([(i.variant_id, i.quantity) for i in items], {i.variant_id: i.variant for i in items})
    problems = {str(i.variant.public_id): [x.code for x in issues[i.variant_id]] for i in items if issues[i.variant_id]}
    if problems:
        raise CartInvalid(fields=problems)
    return items


def _components_total(quote, kind: str) -> Decimal:
    rows = PriceQuoteComponent.objects.filter(item__quote=quote, kind=kind).select_related("item")
    return sum((c.amount_irr * c.item.quantity for c in rows), Decimal(0))


def _discount(items, quote) -> Decimal:
    total = Decimal(0)
    by_variant = {qi.variant_id: qi for qi in quote.items.all()}
    for item in items:
        price = getattr(item.variant, "price", None)
        qi = by_variant.get(item.variant_id)
        if price is not None and qi is not None:
            total += max(price.list_irr - qi.unit_price_irr, Decimal(0)) * item.quantity
    return total


@transaction.atomic
def build_checkout(user, *, address_id, shipping_method: str, currency: str = "IRT", now=None) -> Checkout:
    now = now or timezone.now()
    cart = Cart.objects.select_for_update().filter(user=user, status=CartStatus.ACTIVE).first()
    if cart is None:
        raise BusinessRuleViolation("Your cart is empty.", code="cart_empty")
    items = validate_cart(cart)  # 1-3

    address = Address.objects.filter(public_id=address_id, user=user).first()  # 4 (ownership enforced in the query)
    if address is None:
        raise NotFound("Address not found.")
    validate_address(address)
    ship_kwargs = {"region": address.province, "city": address.city, "postal_code": address.postal_code, "items": shipping.items_from_cart(items)}
    early = shipping.resolve(address.country, Decimal(0), only=shipping_method, **ship_kwargs)
    if not early.options and not shipping.has_subtotal_tiers(shipping_method):
        shipping.quote(shipping_method, address.country, Decimal(0), **ship_kwargs)  # raises the precise reason
    if not Currency.objects.filter(code=currency, is_active=True, is_display=True).exists():
        raise BusinessRuleViolation("Unsupported display currency.", code="unsupported_currency")

    quote = quotes.create_quote(  # 7 (raises PriceUnavailable when a price/rate/rule is missing)
        items=[(i.variant, i.quantity) for i in items],
        currency=currency,
        user=user,
        destination_country=address.country,
        shipping_method=shipping_method,
        now=now,
    )
    ship = shipping.quote(shipping_method, address.country, quote.subtotal_irr, **ship_kwargs)  # 5
    customs = _components_total(quote, "customs")  # 6 (estimate / confirmed flag lives on the quote)
    est_customs, conf_customs = _customs_totals(quote)
    customs_block = international.customs_info(address.country, estimated_irr=est_customs, confirmed_irr=conf_customs)
    discount = _discount(items, quote)
    total = quote.subtotal_irr + quote.separate_irr + ship.fee_irr

    warnings = list(quote.warnings)
    if ship.value_status == "estimated":
        warnings.append("shipping_fee_estimated")
    warnings.append("taxes_not_modeled")
    warnings.extend(international.customs_warnings(customs_block))

    lines = [
        {
            "variant_id": str(qi.variant.public_id),
            "name": qi.product_name,
            "quantity": qi.quantity,
            "unit_price_irr": str(qi.unit_price_irr),
            "line_total_irr": str(qi.line_total_irr),
            "fulfillment_type": qi.variant.fulfillment_type,
        }
        for qi in quote.items.select_related("variant")
    ]
    full_name = getattr(user, "get_full_name", lambda: "")() or address.recipient_name
    checkout = Checkout.objects.create(
        user=user, cart=cart, cart_version=cart.version, cart_fingerprint=fingerprint([(i.variant_id, i.quantity) for i in items]),
        quote=quote, address=address, address_snapshot=address_snapshot(address),
        customer_snapshot={"name": full_name or address.recipient_name, "phone": user.phone or address.phone, "email": user.email or ""},
        shipping_method_code=ship.code, shipping_label=ship.label(), shipping_fee_irr=ship.fee_irr, shipping_value_status=ship.value_status,
        shipping_snapshot={**ship.snapshot(), "customs": customs_block},
        display_currency=currency, merchandise_irr=quote.subtotal_irr, separate_charges_irr=quote.separate_irr,
        customs_estimate_irr=customs, discount_irr=discount, total_irr=total,
        has_estimated_costs=quote.has_estimated_costs or ship.value_status == "estimated",
        warnings=sorted(set(warnings)), summary={"lines": lines}, created_at=now, expires_at=quote.expires_at,
    )  # fmt: skip
    return checkout


def preview(user, **kwargs) -> dict:
    """Same pipeline as `build_checkout`, rolled back: identical numbers, nothing saved, no quote left behind."""
    with transaction.atomic():
        checkout = build_checkout(user, **kwargs)
        body = present(checkout, preview=True)
        transaction.set_rollback(True)
    return body


def present(checkout: Checkout, *, preview: bool = False) -> dict:
    cur = checkout.display_currency
    body = {
        "preview": preview,
        "currency": cur,
        "lines": [
            {**line, "unit_price": money(Decimal(line["unit_price_irr"]), cur), "line_total": money(Decimal(line["line_total_irr"]), cur)}
            for line in checkout.summary.get("lines", [])
        ],
        "address": checkout.address_snapshot,
        "shipping": {
            "method": checkout.shipping_method_code,
            "label": checkout.shipping_label,
            "amount": money(checkout.shipping_fee_irr, cur),
            "status": checkout.shipping_value_status,
        },
        "totals": {
            "merchandise": money(checkout.merchandise_irr, cur),
            "separate_charges": money(checkout.separate_charges_irr, cur),
            "customs_estimate": money(checkout.customs_estimate_irr, cur),
            "discount_included": money(checkout.discount_irr, cur),
            "shipping": money(checkout.shipping_fee_irr, cur),
            "total": money(checkout.total_irr, cur),
            "total_irr": str(checkout.total_irr),
        },
        "delivery": _delivery_block(checkout),
        "customs": _customs_view(checkout, cur),
        "taxes": {"status": "not_modeled"},
        "has_estimated_costs": checkout.has_estimated_costs,
        "warnings": checkout.warnings,
    }
    if not preview:
        body.update(id=str(checkout.public_id), status=checkout.status, expires_at=checkout.expires_at, quote_id=str(checkout.quote.public_id))
    return body


def _delivery_block(checkout: Checkout) -> dict:
    snap = checkout.shipping_snapshot or {}
    return snap.get("delivery") or {"status": "unknown", "min_days": None, "max_days": None, "unit": "business_days"}


def _customs_view(checkout: Checkout, cur: str) -> dict:
    """Estimated / confirmed / payable-on-delivery / unknown stay distinct; an amount exists only when rules produced one."""
    info = dict((checkout.shipping_snapshot or {}).get("customs") or {})
    if not info:
        return {"status": "unknown", "amount": None, "is_estimate": False, "notice": ""}
    amount = info.pop("amount_irr", None)
    info["amount"] = None if amount is None else money(Decimal(amount), cur)
    return info
