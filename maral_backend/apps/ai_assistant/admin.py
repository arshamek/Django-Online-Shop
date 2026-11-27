from django.contrib import admin

from .models import AssistantConfig, Conversation, PurchaseInquiry, UsageRecord


@admin.register(AssistantConfig)
class AssistantConfigAdmin(admin.ModelAdmin):
    list_display = ("enabled", "provider", "model", "updated_at")

    def has_add_permission(self, request):
        return not AssistantConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PurchaseInquiry)
class PurchaseInquiryAdmin(admin.ModelAdmin):
    list_display = ("public_id", "source", "title", "status", "created_at")
    list_filter = ("status", "source")
    readonly_fields = ("public_id", "user", "source", "external_product", "created_at")


@admin.register(UsageRecord)
class UsageRecordAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "provider", "status", "error_code", "demo_mode")
    list_filter = ("status", "provider", "demo_mode")


admin.site.register(Conversation)
