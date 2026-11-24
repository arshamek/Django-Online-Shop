from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.permissions import CsrfRequired
from apps.accounts.views.base import NoStoreMixin
from apps.catalog import selectors
from apps.catalog.context import get_context
from apps.catalog.models import Product
from apps.catalog.presenters import build_card
from apps.cart.services import carts, identity
from apps.cart.services.presenter import build_cart
from apps.common.api.pagination import StandardPagination
from apps.common.exceptions import BusinessRuleViolation

from . import services
from .models import WishlistItem

TAG = ["wishlist"]


class WishlistBase(NoStoreMixin, APIView):
    # Signed-in customers only: the owner is always request.user, never an id sent by the client.
    permission_classes = [IsAuthenticated, CsrfRequired]
    parser_classes = [JSONParser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "cart"


class WishlistView(WishlistBase):
    @extend_schema(tags=TAG, summary="List my wishlist (newest first)", responses={200: dict})
    def get(self, request):
        ctx = get_context(request)
        items = (
            WishlistItem.objects.filter(wishlist__user=request.user, product__in=Product.objects.visible())
            .order_by("-added_at", "-id")
        )
        paginator = StandardPagination()
        page = paginator.paginate_queryset(items, request)
        products = {
            p.pk: p
            for p in selectors.with_card_data(Product.objects.filter(pk__in=[i.product_id for i in page]), ctx)
        }
        results = [{**build_card(products[i.product_id], ctx), "in_wishlist": True, "added_at": i.added_at} for i in page]
        return paginator.get_paginated_response(results)


class WishlistItemView(WishlistBase):
    @extend_schema(tags=TAG, summary="Is this product in my wishlist?", responses={200: dict})
    def get(self, request, product_id):
        return Response({"product_id": str(product_id), "in_wishlist": services.status_for(request.user, [product_id])[str(product_id)]})

    @extend_schema(tags=TAG, summary="Add a product (idempotent)", request=None, responses={200: dict, 201: dict})
    def put(self, request, product_id):
        created = services.add(request.user, services.visible_product(product_id))
        return Response({"product_id": str(product_id), "in_wishlist": True}, status=201 if created else 200)

    @extend_schema(tags=TAG, summary="Remove a product (idempotent)", request=None, responses={204: None})
    def delete(self, request, product_id):
        services.remove(request.user, product_id)
        return Response(status=204)


class StatusView(WishlistBase):
    @extend_schema(tags=TAG, summary="Wishlist status for several products: ?ids=a,b,c", responses={200: dict})
    def get(self, request):
        raw = [x for x in request.query_params.get("ids", "").split(",") if x][:100]
        try:
            ids = [serializers.UUIDField().to_internal_value(x) for x in raw]
        except serializers.ValidationError:
            raise BusinessRuleViolation("ids must be product ids.", code="invalid_ids")
        return Response({"results": services.status_for(request.user, ids)})


class MoveIn(serializers.Serializer):
    variant_id = serializers.UUIDField(required=False)
    size = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    color = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    quantity = serializers.IntegerField(min_value=1, max_value=999, default=1)
    keep_in_wishlist = serializers.BooleanField(default=False)


class MoveToCartView(WishlistBase):
    @extend_schema(tags=TAG, summary="Move a wishlist product to the cart (the server never guesses a size)", request=MoveIn, responses={200: dict})
    def post(self, request, product_id):
        data = MoveIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        if not WishlistItem.objects.filter(wishlist__user=request.user, product__public_id=product_id).exists():
            raise BusinessRuleViolation("This product is not in your wishlist.", code="not_in_wishlist")
        if v.get("variant_id"):
            variant = carts.variant_by_public_id(v["variant_id"])
            if str(variant.product.public_id) != str(product_id):
                raise BusinessRuleViolation("That variant belongs to another product.", code="different_product")
        else:
            variant = carts.resolve_variant(product_id, size=v["size"], color=v["color"])
        cart = identity.get_or_create_user_cart(request.user)
        carts.add_item(cart, variant, v["quantity"])
        if not v["keep_in_wishlist"]:
            services.remove(request.user, product_id)
        return Response(build_cart(cart, get_context(request)))
