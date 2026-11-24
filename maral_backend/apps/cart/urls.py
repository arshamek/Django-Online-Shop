from django.urls import path

from .views import cart

urlpatterns = [
    path("cart", cart.CartView.as_view(), name="cart"),  # GET / POST (create) / DELETE (clear)
    path("cart/items", cart.CartItemsView.as_view(), name="cart-items"),
    path("cart/items/<uuid:line_id>", cart.CartLineView.as_view(), name="cart-line"),
    path("cart/validate", cart.CartValidateView.as_view(), name="cart-validate"),
    path("cart/merge", cart.CartMergeView.as_view(), name="cart-merge"),
]
