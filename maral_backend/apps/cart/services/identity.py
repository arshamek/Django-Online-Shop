"""Who owns the cart this request talks about. Guest ids are random server-issued tokens, never client-chosen."""

import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.pricing.services.quotes import hash_token  # one hashing scheme for carts and price quotes

from ..models import Cart, CartStatus

GUEST_HEADER = "X-Guest-Token"
MIN_TOKEN_LENGTH = 32


def new_guest_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits


def guest_token(request) -> str:
    token = request.headers.get(GUEST_HEADER, "").strip()
    return token if len(token) >= MIN_TOKEN_LENGTH else ""


def guest_expiry():
    return timezone.now() + timedelta(days=settings.CART_GUEST_TTL_DAYS)


def find_active_cart(request) -> Cart | None:
    """A signed-in customer always gets their own cart (a leftover guest token is ignored here, see merge)."""
    user = request.user
    if user.is_authenticated:
        return Cart.objects.filter(user=user, status=CartStatus.ACTIVE).first()
    token = guest_token(request)
    if not token:
        return None
    return (
        Cart.objects.filter(guest_token_hash=hash_token(token), status=CartStatus.ACTIVE)
        .exclude(expires_at__lte=timezone.now())
        .first()
    )


def create_guest_cart() -> tuple[Cart, str]:
    token = new_guest_token()
    cart = Cart.objects.create(guest_token_hash=hash_token(token), expires_at=guest_expiry())
    return cart, token


def get_or_create_user_cart(user) -> Cart:
    cart, _ = Cart.objects.get_or_create(user=user, status=CartStatus.ACTIVE)
    return cart
