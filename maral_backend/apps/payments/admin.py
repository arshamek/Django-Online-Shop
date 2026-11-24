from django.contrib import admin

from .models import Payment, PaymentTransaction


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("public_id", "provider", "amount_irr", "status", "created_at")
    readonly_fields = [f.name for f in Payment._meta.fields]


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ("payment", "kind", "amount_irr", "event_id", "created_at")
