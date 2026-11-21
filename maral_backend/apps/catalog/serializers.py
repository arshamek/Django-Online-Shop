"""
Schema serializers for the public storefront (OpenAPI) and input serializers for the staff API.
Public responses are rendered by presenters.py for speed; tests/test_catalog_schema.py asserts these
documented fields match what the presenters really return.
"""

from rest_framework import serializers

from apps.brands.models import Brand
from apps.categories.models import Category

from .models import FulfillmentType, ImageRights, ProductStatus


class MoneySerializer(serializers.Serializer):
    amount = serializers.CharField(help_text="Decimal string.")
    currency = serializers.CharField()


class PriceSerializer(serializers.Serializer):
    original = MoneySerializer(help_text="Source-store price in the source currency.")
    list = MoneySerializer()
    final = MoneySerializer()
    currency = serializers.CharField(help_text="Display currency of list/final: IRT (default) or IRR.")
    discount_percent = serializers.FloatField()
    range = serializers.DictField(allow_null=True, required=False, help_text="{min,max} when variants differ in price.")
    breakdown = serializers.JSONField(allow_null=True, required=False)


class RefSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    slug = serializers.CharField()
    name = serializers.CharField()


class SourceSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    country_code = serializers.CharField(allow_null=True)
    is_direct_inventory = serializers.BooleanField()


class ImageSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    url = serializers.URLField()
    thumbnail_url = serializers.URLField()
    alt = serializers.CharField()
    width = serializers.IntegerField(allow_null=True)
    height = serializers.IntegerField(allow_null=True)
    attribution = serializers.CharField(allow_null=True)


class SizeSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()
    available = serializers.BooleanField()


class ColorSerializer(SizeSerializer):
    value = serializers.CharField(allow_null=True, help_text="CSS colour swatch.")


class ProductCardSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    slug = serializers.CharField()
    name = serializers.CharField()
    language = serializers.CharField(allow_null=True)
    is_machine_translated = serializers.BooleanField()
    brand = RefSerializer()
    category = RefSerializer()
    images = ImageSerializer(many=True)
    price = PriceSerializer()
    source = SourceSerializer(allow_null=True)
    fulfillment_type = serializers.ChoiceField(choices=[*FulfillmentType.values, "MIXED"])
    fulfillment = serializers.ChoiceField(choices=["stock", "order"])
    available = serializers.BooleanField()
    availability_status = serializers.CharField()
    is_new = serializers.BooleanField()
    sizes = SizeSerializer(many=True)
    colors = ColorSerializer(many=True)


class VariantSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    sku = serializers.CharField()
    size = serializers.DictField(allow_null=True)
    color = serializers.DictField(allow_null=True)
    options = serializers.DictField()
    fulfillment_type = serializers.ChoiceField(choices=FulfillmentType.values)
    source = SourceSerializer()
    available = serializers.BooleanField()
    availability_status = serializers.CharField()
    price = PriceSerializer()
    delivery_estimate = serializers.DictField(allow_null=True)


class BreadcrumbCategorySerializer(RefSerializer):
    breadcrumb = serializers.ListField(child=serializers.DictField(), help_text="Root-first [{slug, name}].")


class ProductDetailSerializer(ProductCardSerializer):
    category = BreadcrumbCategorySerializer()
    short_description = serializers.CharField()
    description = serializers.CharField()
    seo = serializers.DictField()
    specifications = serializers.ListField(child=serializers.DictField())
    variants = VariantSerializer(many=True)
    delivery_estimate = serializers.DictField(allow_null=True)
    reviews = serializers.DictField()
    related = ProductCardSerializer(many=True)


# ------------------------------------------------------------------ staff input
class StaffTranslationInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    short_description = serializers.CharField(max_length=400, allow_blank=True, required=False, default="")
    description = serializers.CharField(allow_blank=True, required=False, default="")
    seo_title = serializers.CharField(max_length=160, allow_blank=True, required=False, default="")
    seo_description = serializers.CharField(max_length=320, allow_blank=True, required=False, default="")
    origin = serializers.ChoiceField(choices=["source", "machine", "human"], default="human")


class StaffProductWriteSerializer(serializers.Serializer):
    slug = serializers.SlugField(max_length=160)
    brand = serializers.SlugRelatedField(slug_field="slug", queryset=Brand.objects.all())
    category = serializers.SlugRelatedField(slug_field="slug", queryset=Category.objects.all())
    source_language = serializers.ChoiceField(choices=["fa", "en", "ar", "tr"], default="fa")
    new_until = serializers.DateTimeField(required=False, allow_null=True)
    manufacturer_model_code = serializers.CharField(max_length=80, required=False, allow_blank=True)
    translations = serializers.DictField(child=StaffTranslationInputSerializer(), required=False)


class StaffPriceInputSerializer(serializers.Serializer):
    original_amount = serializers.DecimalField(max_digits=14, decimal_places=2, min_value=0)
    original_currency = serializers.CharField(max_length=3)
    list_irr = serializers.DecimalField(max_digits=18, decimal_places=0, min_value=1)
    final_irr = serializers.DecimalField(max_digits=18, decimal_places=0, min_value=1)
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, max_value=100, default=0)

    def validate(self, attrs):
        if attrs["final_irr"] > attrs["list_irr"]:
            raise serializers.ValidationError({"final_irr": "Must not exceed list_irr."})
        return attrs


class StaffVariantWriteSerializer(serializers.Serializer):
    sku = serializers.CharField(max_length=80)
    fulfillment_type = serializers.ChoiceField(choices=FulfillmentType.values)
    source = serializers.SlugField(help_text="ProductSource code.")
    size = serializers.CharField(required=False, allow_blank=True)
    color = serializers.CharField(required=False, allow_blank=True)
    source_product_url = serializers.URLField(required=False, allow_blank=True)
    source_sku = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(default=True)
    price = StaffPriceInputSerializer(required=False)
    availability = serializers.ChoiceField(choices=["in_stock", "out_of_stock", "unknown"], required=False)


class StaffStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=ProductStatus.values)


class StaffImageUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    rights = serializers.ChoiceField(choices=ImageRights.values)
    alt = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    attribution = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    source = serializers.SlugField(required=False, allow_blank=True, help_text="ProductSource code.")
    is_published = serializers.BooleanField(default=True)


class StaffImageReorderSerializer(serializers.Serializer):
    order = serializers.ListField(child=serializers.UUIDField(), allow_empty=False, max_length=50)
