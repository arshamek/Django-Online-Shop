from django.contrib import admin

from .models import Currency, ExchangeRate, ExchangeRateSource, RateFetchLog, RateRoute, RateRouteSource

PERM = "accounts.pricing_manage"


class FinancePermissionMixin:
    """Currencies, rates and pricing are managed by holders of `accounts.pricing_manage` (Finance Manager)."""

    def has_module_permission(self, request):
        return request.user.is_active and request.user.has_perm(PERM)

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.has_perm(PERM)

    has_add_permission = has_change_permission = has_view_permission

    def has_delete_permission(self, request, obj=None):
        return self.has_view_permission(request, obj)


@admin.register(Currency)
class CurrencyAdmin(FinancePermissionMixin, admin.ModelAdmin):
    list_display = ("code", "name", "is_source", "is_display", "is_active", "base_currency", "base_factor")
    list_filter = ("is_source", "is_display", "is_active")


@admin.register(ExchangeRateSource)
class SourceAdmin(FinancePermissionMixin, admin.ModelAdmin):
    list_display = (
        "code",
        "provider_key",
        "kind",
        "is_enabled",
        "last_success_at",
        "consecutive_failures",
        "last_error",
    )
    list_filter = ("provider_key", "kind", "is_enabled")
    readonly_fields = ("last_attempt_at", "last_success_at", "last_failure_at", "consecutive_failures", "last_error")


class RouteSourceInline(admin.TabularInline):
    model = RateRouteSource
    extra = 0


@admin.register(RateRoute)
class RouteAdmin(FinancePermissionMixin, admin.ModelAdmin):
    list_display = ("base", "quote", "purpose", "max_age_seconds", "allow_stale_fallback", "is_enabled")
    inlines = [RouteSourceInline]


@admin.register(ExchangeRate)
class RateAdmin(FinancePermissionMixin, admin.ModelAdmin):
    """History is append-only: the admin can read it and add manual rows, never edit or delete."""

    list_display = ("base", "quote", "rate", "source", "rate_at", "status", "is_manual")
    list_filter = ("source", "status", "is_manual", "base")
    date_hierarchy = "rate_at"
    readonly_fields = ("public_id", "fetched_at", "created_by")

    def has_change_permission(self, request, obj=None):
        return obj is None and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        from .services import manual

        manual.set_manual_rate(
            base=obj.base_id,
            quote=obj.quote_id,
            rate=obj.rate,
            actor=request.user,
            note=obj.note or "",
            valid_until=obj.valid_until,
            rate_at=obj.rate_at,
            request=request,
        )


@admin.register(RateFetchLog)
class FetchLogAdmin(FinancePermissionMixin, admin.ModelAdmin):
    list_display = ("source", "started_at", "ok", "n_accepted", "n_quarantined", "n_rejected", "error_code")
    list_filter = ("ok", "source")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
