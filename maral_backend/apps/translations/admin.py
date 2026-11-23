from django.contrib import admin

from apps.catalog.admin import CatalogPermissionMixin

from .models import ProtectedTerm, TranslationCache, TranslationJob, TranslationRecord


@admin.register(TranslationJob)
class JobAdmin(CatalogPermissionMixin, admin.ModelAdmin):
    list_display = ("id", "entity_type", "entity_id", "target_language", "status", "attempts", "provider", "error_code")
    list_filter = ("status", "entity_type", "target_language")
    readonly_fields = [f.name for f in TranslationJob._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(TranslationRecord)
class RecordAdmin(CatalogPermissionMixin, admin.ModelAdmin):
    list_display = ("entity_type", "entity_id", "language", "origin", "status", "provider", "model")
    list_filter = ("entity_type", "origin", "status", "language")


@admin.register(ProtectedTerm)
class ProtectedTermAdmin(CatalogPermissionMixin, admin.ModelAdmin):
    list_display = ("term", "note", "is_active")


@admin.register(TranslationCache)
class CacheAdmin(CatalogPermissionMixin, admin.ModelAdmin):
    list_display = ("target_language", "provider", "model", "hit_count", "last_used_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
