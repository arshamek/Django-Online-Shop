"""
Catalogue domain (docs/architecture/DOMAIN_MODEL.md section 4, docs/database/DATABASE_DESIGN.md 2.3).

Deliberate Phase 4 scope decisions (documented in docs/api/CATALOG_API.md):
* `ProductVariant.fulfillment_type` is stored per variant (DIRECT_INVENTORY | PROXY_PURCHASE) as the Phase 4
  brief requires; DIRECT_THEN_PROXY policies are not modelled.
* `VariantPrice` / `VariantAvailability` hold what the storefront shows. They are written manually (admin /
  staff API) today; the pricing, inventory and imports phases become their writers. Nothing here converts
  currencies or applies margins.
* ExternalProduct / SourcePrice history tables belong to the imports phase and are not created here.
"""

import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.common.languages import LANGUAGE_CHOICES
from apps.common.text import normalize_search_text

RESERVED_SLUGS = frozenset({"search", "filters", "related", "recently-viewed"})


class ProductStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    IN_REVIEW = "in_review", "In review"
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class FulfillmentType(models.TextChoices):
    DIRECT_INVENTORY = "DIRECT_INVENTORY", "Direct inventory (Maral stock)"
    PROXY_PURCHASE = "PROXY_PURCHASE", "Proxy purchase (bought from a source store)"


class AvailabilityStatus(models.TextChoices):
    IN_STOCK = "in_stock", "In stock"
    OUT_OF_STOCK = "out_of_stock", "Out of stock"
    UNKNOWN = "unknown", "Unknown"


# --------------------------------------------------------------------------
# Attributes (size / colour axes + descriptive specifications)
# --------------------------------------------------------------------------
class AttributeValueType(models.TextChoices):
    OPTION = "option", "Option"
    TEXT = "text", "Text"
    NUMBER = "number", "Number"
    BOOL = "bool", "Yes / no"


class ProductAttribute(models.Model):
    code = models.SlugField(max_length=40, unique=True)
    value_type = models.CharField(max_length=10, choices=AttributeValueType.choices, default=AttributeValueType.OPTION)
    is_variant_axis = models.BooleanField(default=False, help_text="Size, colour, volume ... identify a variant.")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "catalog_attribute"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.code


class ProductAttributeTranslation(models.Model):
    attribute = models.ForeignKey(ProductAttribute, on_delete=models.CASCADE, related_name="translations")
    language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES)
    name = models.CharField(max_length=120)

    class Meta:
        db_table = "catalog_attribute_translation"
        constraints = [models.UniqueConstraint(fields=["attribute", "language"], name="attrtr_attr_lang_uniq")]


class AttributeOption(models.Model):
    attribute = models.ForeignKey(ProductAttribute, on_delete=models.CASCADE, related_name="options")
    code = models.CharField(max_length=40, help_text="Stored lower-case (`xl`, `42`, `50ml`, `black`).")
    swatch = models.CharField(max_length=60, blank=True, help_text="CSS colour for colour options.")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "catalog_attribute_option"
        ordering = ["sort_order", "id"]
        constraints = [models.UniqueConstraint(fields=["attribute", "code"], name="attroption_attr_code_uniq")]

    def __str__(self):
        return f"{self.attribute_id}:{self.code}"

    def save(self, *args, **kwargs):
        self.code = self.code.strip().lower()
        super().save(*args, **kwargs)


class AttributeOptionTranslation(models.Model):
    option = models.ForeignKey(AttributeOption, on_delete=models.CASCADE, related_name="translations")
    language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES)
    name = models.CharField(max_length=120)

    class Meta:
        db_table = "catalog_attribute_option_translation"
        constraints = [models.UniqueConstraint(fields=["option", "language"], name="optiontr_opt_lang_uniq")]


# --------------------------------------------------------------------------
# Product
# --------------------------------------------------------------------------
class ProductQuerySet(models.QuerySet):
    def visible(self):
        """Publicly visible products: active, active brand + category, and a listing projection exists
        (the projection only exists while the product has >= 1 active, priced variant)."""
        return self.filter(
            status=ProductStatus.ACTIVE,
            brand__is_active=True,
            category__is_active=True,
            projection__isnull=False,
        )


class Product(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    slug = models.SlugField(max_length=160, unique=True, allow_unicode=False)
    brand = models.ForeignKey("brands.Brand", on_delete=models.PROTECT, related_name="products")
    category = models.ForeignKey("categories.Category", on_delete=models.PROTECT, related_name="products")
    status = models.CharField(max_length=12, choices=ProductStatus.choices, default=ProductStatus.DRAFT)
    source_language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES, default="fa")
    new_until = models.DateTimeField(null=True, blank=True, help_text="Shows the 'new' badge until this moment.")
    manufacturer_model_code = models.CharField(max_length=80, blank=True)
    popularity_score = models.FloatField(default=0, help_text="Written by a ranking job; 0 until real data exists.")
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProductQuerySet.as_manager()

    class Meta:
        db_table = "catalog_product"
        indexes = [
            models.Index(fields=["status", "category"], name="product_status_cat_idx"),
            models.Index(fields=["brand", "status"], name="product_brand_status_idx"),
        ]

    def __str__(self):
        return self.slug

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def clean(self):
        if self.slug in RESERVED_SLUGS:
            raise ValidationError({"slug": f"'{self.slug}' is a reserved route name."})

    @property
    def is_new(self) -> bool:
        return bool(self.new_until and self.new_until > timezone.now())


class TranslationOrigin(models.TextChoices):
    SOURCE = "source", "Source text"
    MACHINE = "machine", "Machine translation"
    HUMAN = "human", "Human translation"


class TranslationStatus(models.TextChoices):
    MACHINE = "machine", "Machine (unreviewed)"
    NEEDS_REVIEW = "needs_review", "Needs review"
    APPROVED = "approved", "Approved"
    HUMAN = "human", "Human"
    STALE = "stale", "Stale (source text changed)"


class ProductTranslation(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="translations")
    language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES)
    name = models.CharField(max_length=255)
    short_description = models.CharField(max_length=400, blank=True)
    description = models.TextField(blank=True)
    seo_title = models.CharField(max_length=160, blank=True)
    seo_description = models.CharField(max_length=320, blank=True)
    search_text = models.CharField(max_length=500, blank=True, editable=False, help_text="Normalised name for search.")
    origin = models.CharField(max_length=10, choices=TranslationOrigin.choices, default=TranslationOrigin.HUMAN)
    status = models.CharField(max_length=14, choices=TranslationStatus.choices, default=TranslationStatus.HUMAN)
    source_text_hash = models.CharField(max_length=64, blank=True)
    provider = models.CharField(max_length=60, blank=True)
    model = models.CharField(max_length=60, blank=True)
    reviewed_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "catalog_product_translation"
        constraints = [models.UniqueConstraint(fields=["product", "language"], name="prodtr_prod_lang_uniq")]
        indexes = [models.Index(fields=["language", "product"], name="prodtr_lang_prod_idx")]

    def __str__(self):
        return f"{self.product_id}:{self.language}"

    def save(self, *args, **kwargs):
        self.search_text = normalize_search_text(self.name)[:500]
        super().save(*args, **kwargs)

    @property
    def is_machine_translated(self) -> bool:
        return self.origin == TranslationOrigin.MACHINE and self.status != TranslationStatus.APPROVED


class ProductAttributeValue(models.Model):
    """A specification row (the 'مشخصات' tab). Free text is stored per language; other types language-less."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="attribute_values")
    attribute = models.ForeignKey(ProductAttribute, on_delete=models.PROTECT, related_name="+")
    option = models.ForeignKey(AttributeOption, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    value_number = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    value_bool = models.BooleanField(null=True, blank=True)
    value_text = models.CharField(max_length=500, blank=True)
    language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "catalog_product_attribute_value"
        ordering = ["sort_order", "id"]
        indexes = [models.Index(fields=["product", "sort_order"], name="attrvalue_prod_sort_idx")]

    def clean(self):
        filled = [
            self.option_id is not None,
            self.value_number is not None,
            self.value_bool is not None,
            bool(self.value_text),
        ]
        if sum(filled) != 1:
            raise ValidationError("Exactly one of option, number, bool or text must be set.")
        if self.value_text and not self.language:
            raise ValidationError({"language": "Free-text specification values need a language."})


# --------------------------------------------------------------------------
# Variants, options, price, availability
# --------------------------------------------------------------------------
class ProductVariant(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField(max_length=80, unique=True)
    option_signature = models.CharField(max_length=64, blank=True, editable=False)
    fulfillment_type = models.CharField(max_length=20, choices=FulfillmentType.choices)
    source = models.ForeignKey("product_sources.ProductSource", on_delete=models.PROTECT, related_name="variants")
    source_product_url = models.URLField(max_length=500, blank=True, help_text="Staff reference only; never exposed.")
    source_sku = models.CharField(max_length=120, blank=True)
    weight_grams = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "catalog_variant"
        ordering = ["sort_order", "id"]
        indexes = [
            models.Index(fields=["product", "is_active"], name="variant_product_active_idx"),
            models.Index(fields=["source", "fulfillment_type"], name="variant_source_fulfil_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "option_signature"],
                condition=~models.Q(option_signature=""),
                name="variant_product_signature_uniq",
            )
        ]

    def __str__(self):
        return self.sku

    def clean(self):
        # A direct-inventory variant keeps its *origin* source for the badge (Trendyol / Namshi / Maral);
        # only a proxy purchase from Maral's own stock is meaningless.
        source = self.source if self.source_id else None
        if (
            source is not None
            and self.fulfillment_type == FulfillmentType.PROXY_PURCHASE
            and source.is_direct_inventory
        ):
            raise ValidationError({"source": "Proxy-purchase variants need an external source store."})


class ProductVariantOption(models.Model):
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name="options")
    attribute = models.ForeignKey(ProductAttribute, on_delete=models.PROTECT, related_name="+")
    option = models.ForeignKey(AttributeOption, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "catalog_variant_option"
        constraints = [models.UniqueConstraint(fields=["variant", "attribute"], name="varopt_variant_attr_uniq")]
        indexes = [models.Index(fields=["option", "variant"], name="varopt_option_variant_idx")]

    def clean(self):
        if self.option_id and self.attribute_id and self.option.attribute_id != self.attribute_id:
            raise ValidationError({"option": "Option does not belong to this attribute."})


class PriceOrigin(models.TextChoices):
    MANUAL = "manual", "Entered manually"
    PRICING_ENGINE = "pricing_engine", "Pricing engine"


class VariantPrice(models.Model):
    """
    Customer-facing price of one variant. Amounts in IRR (accounting currency; Toman = IRR / 10 at the edge).
    `original_*` is the source-store price shown as 'original price' (TRY/AED/USD/EUR/IRT).
    `breakdown` stays NULL until the pricing phase defines fees/FX; the API then returns `breakdown: null`.
    """

    variant = models.OneToOneField(ProductVariant, on_delete=models.CASCADE, related_name="price")
    original_amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    original_currency = models.CharField(max_length=3)
    list_irr = models.DecimalField(max_digits=18, decimal_places=0, validators=[MinValueValidator(Decimal("1"))])
    final_irr = models.DecimalField(max_digits=18, decimal_places=0, validators=[MinValueValidator(Decimal("1"))])
    discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    origin = models.CharField(max_length=16, choices=PriceOrigin.choices, default=PriceOrigin.MANUAL)
    breakdown = models.JSONField(null=True, blank=True)
    computed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "catalog_variant_price"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(final_irr__lte=models.F("list_irr")), name="vprice_final_le_list"
            ),
            models.CheckConstraint(
                condition=models.Q(discount_percent__gte=0, discount_percent__lte=100), name="vprice_discount_range"
            ),
        ]
        indexes = [models.Index(fields=["final_irr"], name="vprice_final_idx")]


class VariantAvailability(models.Model):
    """Catalogue-visible availability. Fed by inventory (DIRECT) or source observations (PROXY) later."""

    variant = models.OneToOneField(ProductVariant, on_delete=models.CASCADE, related_name="availability")
    status = models.CharField(max_length=14, choices=AvailabilityStatus.choices, default=AvailabilityStatus.UNKNOWN)
    observed_at = models.DateTimeField(default=timezone.now)
    origin = models.CharField(max_length=20, default="manual")

    class Meta:
        db_table = "catalog_variant_availability"
        indexes = [models.Index(fields=["status"], name="vavail_status_idx")]


class DeliveryEstimateRule(models.Model):
    """Real business data behind `delivery_estimate`. No rule => the API returns null (never a guess)."""

    fulfillment_type = models.CharField(max_length=20, choices=FulfillmentType.choices)
    source = models.ForeignKey(
        "product_sources.ProductSource", null=True, blank=True, on_delete=models.CASCADE, related_name="+"
    )
    min_days = models.PositiveSmallIntegerField()
    max_days = models.PositiveSmallIntegerField()
    unit = models.CharField(max_length=20, default="working_days")
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "catalog_delivery_estimate_rule"
        constraints = [
            models.CheckConstraint(condition=models.Q(min_days__lte=models.F("max_days")), name="delivery_min_le_max"),
            # Two filtered unique indexes instead of nulls_distinct=False (not supported on SQL Server).
            models.UniqueConstraint(
                fields=["fulfillment_type"], condition=models.Q(source__isnull=True), name="delivery_type_generic_uniq"
            ),
            models.UniqueConstraint(
                fields=["fulfillment_type", "source"],
                condition=models.Q(source__isnull=False),
                name="delivery_type_source_uniq",
            ),
        ]


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------
class ImageRights(models.TextChoices):
    OWNED = "owned", "Owned by Maral"
    LICENSED = "licensed", "Licensed to Maral"
    SOURCE_PERMITTED = "source_permitted", "Copy permitted by the source's terms"


def product_image_path(instance, filename):
    return f"products/{instance.product.public_id}/{uuid.uuid4().hex}.{filename.rsplit('.', 1)[-1].lower()}"


class ProductImageQuerySet(models.QuerySet):
    def published(self):
        """Only images we hold in our own storage, with a rights record, and approved for display."""
        return self.filter(is_published=True).exclude(image="").exclude(image__isnull=True)


class ProductImage(models.Model):
    """
    Authorised assets live in object storage (`image`, `thumbnail`). `origin_url` records where an image
    came from for staff reference and is NEVER served or fetched: copying an external image is only allowed
    when `rights` says so (see services/images.py and ProductSource.allows_image_copy).
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    variant = models.ForeignKey(ProductVariant, null=True, blank=True, on_delete=models.SET_NULL, related_name="images")
    image = models.FileField(upload_to=product_image_path)
    thumbnail = models.FileField(upload_to=product_image_path, blank=True, null=True)
    alt = models.CharField(max_length=255, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    checksum = models.CharField(max_length=64, blank=True)
    rights = models.CharField(max_length=20, choices=ImageRights.choices)
    source = models.ForeignKey(
        "product_sources.ProductSource", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    attribution = models.CharField(max_length=255, blank=True, help_text="Credit line, where the licence requires one.")
    origin_url = models.URLField(max_length=500, blank=True)
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ProductImageQuerySet.as_manager()

    class Meta:
        db_table = "catalog_product_image"
        ordering = ["sort_order", "id"]
        indexes = [models.Index(fields=["product", "sort_order"], name="pimage_product_sort_idx")]

    def clean(self):
        if self.rights == ImageRights.SOURCE_PERMITTED:
            if self.source_id is None or not self.source.allows_image_copy:
                raise ValidationError({"rights": "The source's terms do not permit copying its images."})


# --------------------------------------------------------------------------
# Listing projection (docs/database/INDEX_STRATEGY.md Q1)
# --------------------------------------------------------------------------
class FulfillmentSummary(models.TextChoices):
    DIRECT = "DIRECT_INVENTORY", "Direct inventory"
    PROXY = "PROXY_PURCHASE", "Proxy purchase"
    MIXED = "MIXED", "Mixed"


class ProductListingProjection(models.Model):
    """
    Denormalised row per *publicly visible* product. Written only by services.projection.refresh_projection
    (signals / Celery / `manage.py rebuild_catalog_projection`); rebuildable from the source tables.
    Its existence is what makes a product visible (see ProductQuerySet.visible()).
    """

    product = models.OneToOneField(Product, on_delete=models.CASCADE, primary_key=True, related_name="projection")
    brand = models.ForeignKey("brands.Brand", on_delete=models.CASCADE, related_name="+")
    category = models.ForeignKey("categories.Category", on_delete=models.CASCADE, related_name="+")
    category_path = models.CharField(max_length=255)
    new_until = models.DateTimeField(null=True)
    display_variant = models.ForeignKey(ProductVariant, null=True, on_delete=models.SET_NULL, related_name="+")
    display_source = models.ForeignKey(
        "product_sources.ProductSource", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    display_original_amount = models.DecimalField(max_digits=14, decimal_places=2)
    display_original_currency = models.CharField(max_length=3)
    price_list_irr = models.DecimalField(max_digits=18, decimal_places=0)
    price_final_irr = models.DecimalField(max_digits=18, decimal_places=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    min_final_irr = models.DecimalField(max_digits=18, decimal_places=0)
    max_final_irr = models.DecimalField(max_digits=18, decimal_places=0)
    is_available = models.BooleanField(default=False)
    fulfillment_summary = models.CharField(max_length=20, choices=FulfillmentSummary.choices)
    has_direct_stock = models.BooleanField(default=False)
    popularity_score = models.FloatField(default=0)
    refreshed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "catalog_listing_projection"
        indexes = [
            models.Index(fields=["category", "is_available", "price_final_irr"], name="proj_cat_avail_price_idx"),
            models.Index(fields=["brand", "price_final_irr"], name="proj_brand_price_idx"),
            models.Index(fields=["-popularity_score", "-product"], name="proj_popularity_idx"),
            models.Index(fields=["-new_until"], name="proj_new_until_idx"),
            models.Index(fields=["-discount_percent"], name="proj_discount_idx"),
            models.Index(fields=["category_path"], name="proj_category_path_idx"),
        ]


class ProductListingSource(models.Model):
    """Product <-> source (any active variant), for the source filter and its facet counts."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="listing_sources")
    source = models.ForeignKey("product_sources.ProductSource", on_delete=models.CASCADE, related_name="+")

    class Meta:
        db_table = "catalog_listing_source"
        constraints = [models.UniqueConstraint(fields=["product", "source"], name="listsource_prod_src_uniq")]
        indexes = [models.Index(fields=["source", "product"], name="listsource_src_prod_idx")]
