from django.contrib import admin

from .models import (
    Payment,
    PaymentProviderConfig,
    PaymentRefund,
    PaymentTransaction,
    ProviderCallback,
    ReconciliationRun,
)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = ("public_id", "provider", "amount_irr", "charge_currency", "status", "needs_review", "created_at")
    list_filter = ("status", "provider", "needs_review")
    search_fields = ("public_id", "provider_ref")


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(ReadOnlyAdmin):
    list_display = ("payment", "kind", "amount_irr", "event_id", "created_at")
    list_filter = ("kind",)


@admin.register(PaymentRefund)
class PaymentRefundAdmin(ReadOnlyAdmin):
    list_display = ("public_id", "payment", "status", "amount_irr", "charge_currency", "created_at")
    list_filter = ("status",)


@admin.register(ProviderCallback)
class ProviderCallbackAdmin(ReadOnlyAdmin):
    list_display = ("provider", "outcome", "reason", "event_id", "received_at")
    list_filter = ("provider", "outcome")


@admin.register(ReconciliationRun)
class ReconciliationRunAdmin(ReadOnlyAdmin):
    list_display = ("started_at", "provider", "scanned", "applied", "errors")


@admin.register(PaymentProviderConfig)
class PaymentProviderConfigAdmin(admin.ModelAdmin):
    """Configuration only. There is deliberately no credential field; secrets live in the environment."""

    list_display = ("key", "is_enabled", "mode", "eligibility_status", "sandbox_verified_at")
    list_filter = ("is_enabled", "eligibility_status", "mode")
