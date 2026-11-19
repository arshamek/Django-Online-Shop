from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from apps.audit import services as audit
from apps.audit.models import AuditLog

from .models import Address, CustomerProfile, User, UserSession


class UserCreationForm(AdminUserCreationForm):
    class Meta:
        model = User
        fields = ("email", "phone")


class UserForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = UserForm
    add_form = UserCreationForm
    ordering = ("-created_at",)
    list_display = ("email", "phone", "first_name", "last_name", "status", "is_staff", "created_at")
    list_filter = ("status", "is_staff", "is_superuser", "groups")
    search_fields = ("email", "phone", "first_name", "last_name")
    readonly_fields = (
        "public_id",
        "last_login",
        "created_at",
        "updated_at",
        "password_changed_at",
        "failed_login_count",
        "locked_until",
    )
    fieldsets = (
        (None, {"fields": ("email", "phone", "password")}),
        ("Personal", {"fields": ("first_name", "last_name")}),
        (
            "State",
            {"fields": ("status", "email_verified_at", "phone_verified_at", "failed_login_count", "locked_until")},
        ),
        ("Access", {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("public_id", "last_login", "created_at", "updated_at", "password_changed_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "phone", "password1", "password2", "is_staff")}),
    )

    def save_model(self, request, obj, form, change):
        sensitive = {"is_staff", "is_superuser", "status"} & set(form.changed_data)
        super().save_model(request, obj, form, change)
        if sensitive or not change:
            audit.record(
                "staff.user.created" if not change else "staff.user.access_changed",
                request=request,
                target=obj,
                fields=sorted(sensitive),
            )


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "action", "actor_public_id", "target_type", "target_id", "ip_address")
    list_filter = ("action",)
    search_fields = ("target_id", "request_id", "action")


@admin.register(UserSession)
class UserSessionAdmin(ReadOnlyAdmin):
    list_display = ("user", "created_at", "last_seen_at", "ip_address", "revoked_at", "revoked_reason")


admin.site.register(CustomerProfile)
admin.site.register(Address)
_ = forms
