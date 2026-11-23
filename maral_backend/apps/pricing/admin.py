from django.contrib import admin, messages

from apps.currencies.admin import FinancePermissionMixin

from .models import PriceHistory, PriceQuote, PricingRule, PricingRuleSet
from .services import rulesets


class RuleInline(admin.TabularInline):
    model = PricingRule
    extra = 0
    fields = ("code", "kind", "calc_method", "amount", "currency", "value_status", "charge_mode", "is_enabled")

    def has_change_permission(self, request, obj=None):
        return obj is not None and obj.is_editable and super().has_change_permission(request, obj)

    def has_add_permission(self, request, obj=None):
        return obj is not None and obj.is_editable and super().has_add_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return obj is not None and obj.is_editable and super().has_delete_permission(request, obj)


@admin.register(PricingRuleSet)
class RuleSetAdmin(FinancePermissionMixin, admin.ModelAdmin):
    """Published sets are read-only. Use the 'Clone into a draft' action, edit, then 'Publish'."""

    list_display = (
        "version",
        "name",
        "status",
        "profit_method",
        "target_profit_percent",
        "estimates_policy",
        "published_at",
    )
    inlines = [RuleInline]
    actions = ["clone_into_draft", "publish_draft"]

    def get_readonly_fields(self, request, obj=None):
        base = ("version", "status", "published_at", "published_by", "cloned_from", "created_by", "effective_from")
        if obj is not None and not obj.is_editable:
            return base + tuple(f.name for f in PricingRuleSet._meta.fields if f.name not in base)
        return base

    def has_delete_permission(self, request, obj=None):
        return obj is not None and obj.is_editable and super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Clone into a new draft")
    def clone_into_draft(self, request, queryset):
        for rs in queryset:
            draft = rulesets.create_draft(actor=request.user, from_set=rs, request=request)
            self.message_user(request, f"Draft v{draft.version} created from v{rs.version}.", messages.SUCCESS)

    @admin.action(description="Publish selected draft (becomes immutable and active)")
    def publish_draft(self, request, queryset):
        for rs in queryset:
            try:
                rulesets.publish(rs, actor=request.user, request=request)
                self.message_user(request, f"v{rs.version} published.", messages.SUCCESS)
            except Exception as exc:  # noqa: BLE001
                self.message_user(request, f"v{rs.version}: {getattr(exc, 'detail', exc)}", messages.ERROR)


@admin.register(PriceQuote)
class QuoteAdmin(FinancePermissionMixin, admin.ModelAdmin):
    list_display = ("public_id", "status", "rule_version", "total_irr", "display_currency", "created_at", "expires_at")
    list_filter = ("status", "display_currency")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PriceHistory)
class HistoryAdmin(FinancePermissionMixin, admin.ModelAdmin):
    list_display = (
        "variant",
        "sequence",
        "outcome",
        "cause",
        "old_final_irr",
        "new_final_irr",
        "rule_version",
        "created_at",
    )
    list_filter = ("outcome", "cause")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
