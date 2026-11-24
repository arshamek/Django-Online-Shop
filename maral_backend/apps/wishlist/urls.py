from django.urls import path

from . import views

urlpatterns = [
    path("wishlist", views.WishlistView.as_view(), name="wishlist"),
    path("wishlist/status", views.StatusView.as_view(), name="wishlist-status"),
    path("wishlist/items/<uuid:product_id>", views.WishlistItemView.as_view(), name="wishlist-item"),
    path("wishlist/items/<uuid:product_id>/move-to-cart", views.MoveToCartView.as_view(), name="wishlist-move"),
]
