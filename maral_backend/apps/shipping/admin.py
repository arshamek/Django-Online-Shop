from django.contrib import admin

from .models import (
    CountryShippingPolicy,
    Shipment,
    ShipmentEvent,
    ShippingMethod,
    ShippingRate,
    ShippingZone,
)


class RateInline(admin.TabularInline):
    model = ShippingRate
    extra = 0


@admin.register(ShippingMethod)
class ShippingMethodAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "kind",
        "provider_key",
        "fee_irr",
        "free_over_irr",
        "value_status",
        "requires_tracking",
        "is_active",
        "sort_order",
    )
    inlines = [RateInline]


@admin.register(ShippingZone)
class ShippingZoneAdmin(admin.ModelAdmin):
    list_display = ("code", "countries", "priority", "is_active")


@admin.register(CountryShippingPolicy)
class CountryShippingPolicyAdmin(admin.ModelAdmin):
    list_display = ("country", "is_shippable", "customs_mode", "is_active")


class EventInline(admin.TabularInline):
    model = ShipmentEvent
    extra = 0
    can_delete = False
    readonly_fields = [f.name for f in ShipmentEvent._meta.fields]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ("public_id", "order", "carrier", "tracking_number", "status", "created_at")
    list_filter = ("status", "provider_key")
    readonly_fields = [f.name for f in Shipment._meta.fields]
    inlines = [EventInline]

    def has_add_permission(self, request):
        return False  # shipments are changed through the service (status machine + audit)
