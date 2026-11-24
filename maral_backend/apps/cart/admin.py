from django.contrib import admin

from .models import Cart, CartItem


class ItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    readonly_fields = ("variant", "quantity")


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("public_id", "user", "status", "version", "updated_at")
    readonly_fields = ("guest_token_hash",)
    inlines = [ItemInline]
