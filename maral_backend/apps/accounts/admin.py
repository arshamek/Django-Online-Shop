from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "phone", "is_staff", "is_active")
    search_fields = ("username", "email", "phone")
    fieldsets = DjangoUserAdmin.fieldsets + (("Maral", {"fields": ("phone", "public_id")}),)
    readonly_fields = ("public_id",)
