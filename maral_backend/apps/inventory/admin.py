from django.contrib import admin

from .models import InventoryItem, InventoryMovement, InventoryReservation, Warehouse


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_active", "is_default")


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ("variant", "warehouse", "on_hand", "reserved", "safety_stock")
    readonly_fields = ("on_hand", "reserved")  # change stock through the adjust/receive services so it is audited
    search_fields = ("variant__sku",)


@admin.register(InventoryMovement)
class InventoryMovementAdmin(admin.ModelAdmin):
    list_display = ("created_at", "item", "movement_type", "on_hand_delta", "reserved_delta", "reference_id")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


@admin.register(InventoryReservation)
class InventoryReservationAdmin(admin.ModelAdmin):
    list_display = ("holder_type", "holder_id", "item", "quantity", "status", "expires_at")
