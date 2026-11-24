"""Cart operations. Every mutation locks the cart row, re-checks availability on the server and bumps `version`."""

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.catalog.models import Product, ProductVariant, ProductVariantOption
from apps.common.exceptions import BusinessRuleViolation, Conflict

from ..models import Cart, CartItem, CartStatus
from . import availability


class CartError(BusinessRuleViolation):
    pass


def _lock(cart: Cart) -> Cart:
    locked = Cart.objects.select_for_update().get(pk=cart.pk)
    if locked.status != CartStatus.ACTIVE:
        raise Conflict("This cart is no longer active.", code="cart_inactive")
    return locked


def _bump(cart: Cart) -> None:
    updates = {"version": F("version") + 1, "updated_at": timezone.now()}
    if cart.user_id is None:
        from .identity import guest_expiry

        updates["expires_at"] = guest_expiry()
    Cart.objects.filter(pk=cart.pk).update(**updates)
    cart.refresh_from_db()


def _require_purchasable(variant: ProductVariant, quantity: int) -> None:
    issues = availability.assess([(variant.pk, quantity)]).get(variant.pk, [])
    if issues:
        first = issues[0]
        raise Conflict(first.message, code=first.code) if first.code in ("out_of_stock", "insufficient_stock", "unavailable") else CartError(first.message, code=first.code)


# ------------------------------------------------------------------------------------------- selecting a variant
def resolve_variant(product_public_id, *, size: str = "", color: str = "") -> ProductVariant:
    """Frontend sends product + size + colour; the server finds the one variant (never trusts a client SKU/price)."""
    product = Product.objects.visible().filter(public_id=product_public_id).first()
    if product is None:
        raise BusinessRuleViolation("Product not found.", code="product_not_found")
    variants = list(
        ProductVariant.objects.filter(product=product, is_active=True).prefetch_related("options__attribute", "options__option")
    )
    axes: dict[str, set[str]] = {}
    for v in variants:
        for o in v.options.all():
            if o.attribute.is_variant_axis:
                axes.setdefault(o.attribute.code, set()).add(o.option.code)
    chosen = {"size": size.strip().lower(), "color": color.strip().lower()}
    missing = [code for code, values in axes.items() if len(values) > 1 and not chosen.get(code)]
    if missing:
        raise BusinessRuleViolation(
            "Choose all options first.", code="selection_incomplete", fields={code: ["Required."] for code in missing}
        )
    matches = [
        v
        for v in variants
        if all(
            {o.attribute.code: o.option.code for o in v.options.all() if o.attribute.is_variant_axis}.get(code) == value
            for code, value in chosen.items()
            if value
        )
    ]
    if not matches:
        raise BusinessRuleViolation("This combination does not exist.", code="variant_not_found")
    if len(matches) > 1:
        raise BusinessRuleViolation("The selection is ambiguous.", code="selection_ambiguous")
    return matches[0]


def variant_by_public_id(public_id) -> ProductVariant:
    variant = ProductVariant.objects.filter(public_id=public_id, is_active=True).select_related("product").first()
    if variant is None:
        raise BusinessRuleViolation("Variant not found.", code="variant_not_found")
    return variant


# ------------------------------------------------------------------------------------------- mutations
@transaction.atomic
def add_item(cart: Cart, variant: ProductVariant, quantity: int) -> CartItem:
    cart = _lock(cart)
    line = CartItem.objects.filter(cart=cart, variant=variant).first()
    if line is None and cart.items.count() >= settings.CART_MAX_LINES:
        raise CartError(f"A cart holds at most {settings.CART_MAX_LINES} different items.", code="cart_full")
    new_quantity = (line.quantity if line else 0) + quantity
    if new_quantity > availability.max_quantity():
        raise CartError(f"At most {availability.max_quantity()} per item.", code="max_quantity_exceeded")
    _require_purchasable(variant, new_quantity)
    if line is None:
        line = CartItem.objects.create(cart=cart, variant=variant, quantity=quantity)
    else:
        line.quantity = new_quantity
        line.save(update_fields=["quantity", "updated_at"])
    _bump(cart)
    return line


def _line(cart: Cart, line_id) -> CartItem:
    line = CartItem.objects.select_related("variant").filter(cart=cart, public_id=line_id).first()
    if line is None:
        raise BusinessRuleViolation("Cart line not found.", code="line_not_found")
    return line


@transaction.atomic
def set_quantity(cart: Cart, line_id, quantity: int) -> CartItem:
    cart = _lock(cart)
    line = _line(cart, line_id)
    if quantity > availability.max_quantity():
        raise CartError(f"At most {availability.max_quantity()} per item.", code="max_quantity_exceeded")
    if quantity > line.quantity:  # lowering a quantity is always allowed, even if the item went out of stock
        _require_purchasable(line.variant, quantity)
    line.quantity = quantity
    line.save(update_fields=["quantity", "updated_at"])
    _bump(cart)
    return line


@transaction.atomic
def change_variant(cart: Cart, line_id, new_variant: ProductVariant) -> CartItem:
    """Switch the size / colour of a line. Merges into an existing line of the target variant."""
    cart = _lock(cart)
    line = _line(cart, line_id)
    if new_variant.product_id != line.variant.product_id:
        raise CartError("A line can only switch to another variant of the same product.", code="different_product")
    if new_variant.pk == line.variant_id:
        return line
    other = CartItem.objects.filter(cart=cart, variant=new_variant).first()
    quantity = line.quantity + (other.quantity if other else 0)
    quantity = min(quantity, availability.max_quantity())
    _require_purchasable(new_variant, quantity)
    if other:
        other.quantity = quantity
        other.save(update_fields=["quantity", "updated_at"])
        line.delete()
        line = other
    else:
        line.variant = new_variant
        line.quantity = quantity
        line.save(update_fields=["variant", "quantity", "updated_at"])
    _bump(cart)
    return line


@transaction.atomic
def remove_item(cart: Cart, line_id) -> None:
    cart = _lock(cart)
    _line(cart, line_id).delete()
    _bump(cart)


@transaction.atomic
def clear(cart: Cart) -> None:
    cart = _lock(cart)
    cart.items.all().delete()
    _bump(cart)


@transaction.atomic
def mark_converted(cart: Cart) -> None:
    """Checkout succeeded: the cart is spent; the customer's next cart starts empty."""
    cart = Cart.objects.select_for_update().get(pk=cart.pk)
    cart.status = CartStatus.CONVERTED
    cart.save(update_fields=["status", "updated_at"])


# ------------------------------------------------------------------------------------------- merge after login
@transaction.atomic
def merge_guest_into_user(guest: Cart, user) -> tuple[Cart, list[dict]]:
    """
    Folds a guest cart into the customer's cart: quantities add up (capped), the guest cart is closed and its token
    stops working. Re-running it is harmless. Returns (customer cart, lines that had to be capped).
    """
    from . import identity

    if guest.user_id is not None:
        raise BusinessRuleViolation("Only a guest cart can be merged.", code="not_a_guest_cart")
    customer = identity.get_or_create_user_cart(user)
    first, second = sorted([guest.pk, customer.pk])
    locked = {c.pk: c for c in Cart.objects.select_for_update().filter(pk__in=[first, second]).order_by("pk")}
    guest, customer = locked[guest.pk], locked[customer.pk]
    if guest.status == CartStatus.MERGED:
        return customer, []  # already done (double click / retry)
    if guest.status != CartStatus.ACTIVE:
        raise Conflict("This cart is no longer active.", code="cart_inactive")
    adjusted: list[dict] = []
    cap = availability.max_quantity()
    existing = {i.variant_id: i for i in customer.items.all()}
    for item in guest.items.select_related("variant"):
        target = existing.get(item.variant_id)
        total = item.quantity + (target.quantity if target else 0)
        if target is None and len(existing) >= settings.CART_MAX_LINES:
            adjusted.append({"variant_id": str(item.variant.public_id), "reason": "cart_full", "dropped": True})
            continue
        if total > cap:
            adjusted.append({"variant_id": str(item.variant.public_id), "reason": "max_quantity", "quantity": cap})
            total = cap
        if target:
            target.quantity = total
            target.save(update_fields=["quantity", "updated_at"])
        else:
            existing[item.variant_id] = CartItem.objects.create(cart=customer, variant=item.variant, quantity=total)
    guest.status, guest.merged_into = CartStatus.MERGED, customer
    guest.save(update_fields=["status", "merged_into", "updated_at"])
    guest.items.all().delete()
    _bump(customer)
    return customer, adjusted
