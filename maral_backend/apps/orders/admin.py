from django.contrib import admin

from .models import Checkout, Order, OrderItem, OrderStatusHistory, Refund


class ReadOnly(admin.ModelAdmin):
    """Orders are changed through the staff API / services (state machine, audit); the admin only looks."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    fields = ("product_name", "sku", "quantity", "unit_price_irr", "line_total_irr")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(ReadOnly):
    list_display = ("code", "order_type", "status", "total_irr", "placed_at")
    list_filter = ("status", "order_type")
    search_fields = ("code", "user__email", "user__phone")
    inlines = [ItemInline]


@admin.register(Checkout)
class CheckoutAdmin(ReadOnly):
    list_display = ("public_id", "user", "status", "total_irr", "created_at")


@admin.register(Refund)
class RefundAdmin(ReadOnly):
    list_display = ("public_id", "order", "kind", "status", "amount_irr")


@admin.register(OrderStatusHistory)
class HistoryAdmin(ReadOnly):
    list_display = ("order", "from_status", "to_status", "actor_type", "created_at")
