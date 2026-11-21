from django.contrib import admin

from apps.brands.models import Brand, BrandAlias, BrandTranslation
from apps.categories.models import Category, CategoryTranslation
from apps.product_sources.models import ProductSource
from apps.reviews.models import Review

from .models import (
    AttributeOption,
    AttributeOptionTranslation,
    DeliveryEstimateRule,
    Product,
    ProductAttribute,
    ProductAttributeTranslation,
    ProductAttributeValue,
    ProductImage,
    ProductTranslation,
    ProductVariant,
    ProductVariantOption,
    VariantAvailability,
    VariantPrice,
)

PERM = "accounts.catalog_manage"


class CatalogPermissionMixin:
    """Every catalogue model is managed by holders of `accounts.catalog_manage` only (no per-model perms)."""

    def has_module_permission(self, request):
        return request.user.is_active and request.user.has_perm(PERM)

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.has_perm(PERM)

    has_add_permission = has_change_permission = has_delete_permission = has_view_permission


class Base(CatalogPermissionMixin, admin.ModelAdmin):
    pass


class TabularBase(CatalogPermissionMixin, admin.TabularInline):
    extra = 0


class ProductTranslationInline(TabularBase):
    model = ProductTranslation
    fields = ("language", "name", "short_description", "origin", "status")


class ProductImageInline(TabularBase):
    model = ProductImage
    fields = ("image", "alt", "sort_order", "rights", "source", "attribution", "is_published")


class ProductAttributeValueInline(TabularBase):
    model = ProductAttributeValue


@admin.register(Product)
class ProductAdmin(Base):
    list_display = ("slug", "brand", "category", "status", "updated_at")
    list_filter = ("status", "brand", "category")
    search_fields = ("slug", "translations__name", "variants__sku")
    list_select_related = ("brand", "category")
    inlines = [ProductTranslationInline, ProductImageInline, ProductAttributeValueInline]
    actions = ["activate", "archive"]

    @admin.action(description="Activate (publish) selected products")
    def activate(self, request, queryset):
        from .services import publishing

        for product in queryset:
            try:
                publishing.change_status(product, "active")
            except Exception as exc:  # surfaced to the operator, one message per product
                self.message_user(request, f"{product.slug}: {exc}", level="error")

    @admin.action(description="Archive selected products")
    def archive(self, request, queryset):
        from .services import publishing

        for product in queryset:
            publishing.change_status(product, "archived")


class VariantOptionInline(TabularBase):
    model = ProductVariantOption


class VariantPriceInline(CatalogPermissionMixin, admin.StackedInline):
    model = VariantPrice


class VariantAvailabilityInline(CatalogPermissionMixin, admin.StackedInline):
    model = VariantAvailability


@admin.register(ProductVariant)
class ProductVariantAdmin(Base):
    list_display = ("sku", "product", "fulfillment_type", "source", "is_active")
    list_filter = ("fulfillment_type", "source", "is_active")
    search_fields = ("sku", "product__slug")
    inlines = [VariantOptionInline, VariantPriceInline, VariantAvailabilityInline]


class CategoryTranslationInline(TabularBase):
    model = CategoryTranslation


@admin.register(Category)
class CategoryAdmin(Base):
    list_display = ("slug", "parent", "sort_order", "is_active")
    inlines = [CategoryTranslationInline]


class BrandTranslationInline(TabularBase):
    model = BrandTranslation


class BrandAliasInline(TabularBase):
    model = BrandAlias


@admin.register(Brand)
class BrandAdmin(Base):
    list_display = ("name", "slug", "is_active")
    search_fields = ("name", "slug")
    inlines = [BrandTranslationInline, BrandAliasInline]


@admin.register(ProductSource)
class ProductSourceAdmin(Base):
    list_display = ("code", "name", "integration_type", "base_currency", "allows_image_copy", "is_active")


class AttributeTranslationInline(TabularBase):
    model = ProductAttributeTranslation


@admin.register(ProductAttribute)
class ProductAttributeAdmin(Base):
    list_display = ("code", "value_type", "is_variant_axis")
    inlines = [AttributeTranslationInline]


class OptionTranslationInline(TabularBase):
    model = AttributeOptionTranslation


@admin.register(AttributeOption)
class AttributeOptionAdmin(Base):
    list_display = ("attribute", "code", "swatch", "sort_order")
    list_filter = ("attribute",)
    inlines = [OptionTranslationInline]


@admin.register(DeliveryEstimateRule)
class DeliveryEstimateRuleAdmin(Base):
    list_display = ("fulfillment_type", "source", "min_days", "max_days", "unit", "is_active")


@admin.register(Review)
class ReviewAdmin(Base):
    list_display = ("product", "rating", "status", "publication_rights", "created_at")
    list_filter = ("status", "publication_rights")
