import uuid

from django.conf import settings
from django.db import models


class Wishlist(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wishlist")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "wishlist_wishlist"


class WishlistItem(models.Model):
    wishlist = models.ForeignKey(Wishlist, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="+")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "wishlist_item"
        ordering = ["-added_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["wishlist", "product"], name="wishitem_wl_product_uniq")]
