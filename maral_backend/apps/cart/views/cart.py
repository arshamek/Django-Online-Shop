from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.parsers import JSONParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.permissions import CsrfRequired
from apps.accounts.views.base import NoStoreMixin
from apps.catalog.context import get_context
from apps.common.exceptions import BusinessRuleViolation

from ..models import Cart
from ..services import availability, carts, identity
from ..services.presenter import build_cart

TAG = ["cart"]


class CartBase(NoStoreMixin, APIView):
    permission_classes = [AllowAny, CsrfRequired]
    parser_classes = [JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "cart"

    def existing_cart(self, request) -> Cart:
        cart = identity.find_active_cart(request)
        if cart is None:
            raise BusinessRuleViolation("No cart found for this request.", code="cart_not_found")
        return cart

    def cart_or_new(self, request):
        """(cart, guest_token_if_just_created)."""
        cart = identity.find_active_cart(request)
        if cart is not None:
            return cart, None
        if request.user.is_authenticated:
            return identity.get_or_create_user_cart(request.user), None
        return identity.create_guest_cart()

    def render(self, request, cart, token=None, status=200):
        return Response(build_cart(cart, get_context(request), guest_token=token), status=status)


class ItemIn(serializers.Serializer):
    variant_id = serializers.UUIDField(required=False)
    product_id = serializers.UUIDField(required=False)
    size = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    color = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    quantity = serializers.IntegerField(min_value=1, max_value=999, default=1)
    # Anything else a client sends (price, total, discount ...) is not a field here and is ignored.

    def validate(self, attrs):
        if not attrs.get("variant_id") and not attrs.get("product_id"):
            raise serializers.ValidationError("Send variant_id, or product_id with size/color.")
        return attrs


def variant_from(data):
    if data.get("variant_id"):
        return carts.variant_by_public_id(data["variant_id"])
    return carts.resolve_variant(data["product_id"], size=data.get("size", ""), color=data.get("color", ""))


class CartView(CartBase):
    @property
    def throttle_scope(self):  # creating anonymous carts is the abusable action: stricter budget
        return "cart_create" if self.request.method == "POST" else "cart"

    @extend_schema(tags=TAG, summary="Create (or fetch) your cart. Guests receive a one-time guest_token.", request=None, responses={200: dict, 201: dict})
    def post(self, request):
        existing = identity.find_active_cart(request)
        if existing is not None:
            return self.render(request, existing)
        cart, token = self.cart_or_new(request)
        return self.render(request, cart, token, status=201)

    @extend_schema(tags=TAG, summary="Retrieve the cart (prices recalculated by the server)", responses={200: dict})
    def get(self, request):
        cart = identity.find_active_cart(request)
        if cart is None and request.user.is_authenticated:
            cart = identity.get_or_create_user_cart(request.user)
        if cart is None:
            raise BusinessRuleViolation("No cart found for this request.", code="cart_not_found")
        return self.render(request, cart)

    @extend_schema(tags=TAG, summary="Empty the cart", request=None, responses={200: dict})
    def delete(self, request):
        cart = self.existing_cart(request)
        carts.clear(cart)
        return self.render(request, cart)


class CartItemsView(CartBase):
    @extend_schema(tags=TAG, summary="Add a product (variant, or product + size + colour)", request=ItemIn, responses={201: dict})
    def post(self, request):
        data = ItemIn(data=request.data)
        data.is_valid(raise_exception=True)
        variant = variant_from(data.validated_data)
        cart, token = self.cart_or_new(request)
        carts.add_item(cart, variant, data.validated_data["quantity"])
        return self.render(request, cart, token, status=201)


class LinePatchIn(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1, max_value=999, required=False)
    variant_id = serializers.UUIDField(required=False)
    size = serializers.CharField(max_length=40, required=False, allow_blank=True)
    color = serializers.CharField(max_length=40, required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Nothing to change.")
        return attrs


class CartLineView(CartBase):
    @extend_schema(tags=TAG, summary="Change quantity and/or select another variant (size, colour)", request=LinePatchIn, responses={200: dict})
    def patch(self, request, line_id):
        data = LinePatchIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        cart = self.existing_cart(request)
        line = carts._line(cart, line_id)
        if v.get("variant_id") or "size" in v or "color" in v:
            if v.get("variant_id"):
                target = carts.variant_by_public_id(v["variant_id"])
            else:
                current = {o.attribute.code: o.option.code for o in line.variant.options.all()}
                target = carts.resolve_variant(
                    line.variant.product.public_id,
                    size=v.get("size", current.get("size", "")),
                    color=v.get("color", current.get("color", "")),
                )
            line = carts.change_variant(cart, line_id, target)
            line_id = line.public_id
        if "quantity" in v:
            carts.set_quantity(cart, line_id, v["quantity"])
        cart.refresh_from_db()
        return self.render(request, cart)

    @extend_schema(tags=TAG, summary="Remove a line", request=None, responses={200: dict})
    def delete(self, request, line_id):
        cart = self.existing_cart(request)
        carts.remove_item(cart, line_id)
        return self.render(request, cart)


class CartValidateView(CartBase):
    @extend_schema(tags=TAG, summary="Re-check availability of every line right now", request=None, responses={200: dict})
    def post(self, request):
        cart = self.existing_cart(request)
        body = build_cart(cart, get_context(request))
        return Response(
            {
                "valid": body["checkout_ready"],
                "issues": [
                    {"line_id": line["line_id"], "variant_id": line["variant_id"], "issues": line["issues"]}
                    for line in body["items"]
                    if line["issues"]
                ],
                "version": body["version"],
            }
        )


class CartMergeView(CartBase):
    @extend_schema(tags=TAG, summary="After login: merge the guest cart (X-Guest-Token header) into the customer cart", request=None, responses={200: dict})
    def post(self, request):
        if not request.user.is_authenticated:
            raise BusinessRuleViolation("Sign in before merging a guest cart.", code="authentication_required")
        token = identity.guest_token(request)
        if not token:
            raise BusinessRuleViolation("Send the guest cart token in the X-Guest-Token header.", code="guest_token_required")
        from apps.pricing.services.quotes import hash_token

        guest = Cart.objects.filter(guest_token_hash=hash_token(token), user__isnull=True).first()
        if guest is None:
            raise BusinessRuleViolation("Guest cart not found.", code="cart_not_found")  # wrong token looks like no cart
        customer, adjusted = carts.merge_guest_into_user(guest, request.user)
        body = build_cart(customer, get_context(request))
        body["merge"] = {"adjusted": adjusted}
        return Response(body)
