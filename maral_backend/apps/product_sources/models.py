import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class IntegrationType(models.TextChoices):
    INTERNAL = "internal", "Maral's own inventory"
    MANUAL = "manual", "Manual entry"
    OFFICIAL_API = "official_api", "Official API"
    LICENSED_FEED = "licensed_feed", "Licensed feed"


class ProductSource(models.Model):
    """
    Where a variant is sourced from. `maral` (internal) is the pseudo-source for direct inventory.
    Only official APIs / licensed feeds / manual entry exist as integration types: scraping is not an
    option (docs/architecture/TECHNICAL_DECISIONS.md). `terms_note` records the licence/permission.
    """

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    country_code = models.CharField(max_length=2, blank=True, help_text="ISO 3166-1 alpha-2.")
    base_currency = models.CharField(max_length=3, blank=True, help_text="ISO 4217 (TRY, AED, USD, EUR, IRT).")
    integration_type = models.CharField(max_length=20, choices=IntegrationType.choices, default=IntegrationType.MANUAL)
    is_active = models.BooleanField(default=True)
    allows_image_copy = models.BooleanField(
        default=False,
        help_text="True only when the source's written terms permit copying its images into our storage.",
    )
    terms_note = models.TextField(blank=True, help_text="Licence / permission record for this source.")
    rate_limit_per_min = models.PositiveIntegerField(
        default=60,
        help_text="Our own ceiling on calls per minute to this source. Keep it at or below the source's published limit.",
    )
    credential_ref = models.CharField(
        max_length=40,
        blank=True,
        help_text="Name of the credential set in settings.IMPORTS_CREDENTIALS. Secrets never live in the database.",
    )
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_sources_source"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.code

    @property
    def is_direct_inventory(self) -> bool:
        return self.integration_type == IntegrationType.INTERNAL


# --------------------------------------------------------------------------
# Source listings (docs/database/DATABASE_DESIGN.md 2.4). A listing is what a source store shows; it is
# never merged into a canonical catalog.Product by name (DOMAIN_MODEL 4, rule 1).
# --------------------------------------------------------------------------
class MatchStatus(models.TextChoices):
    UNMATCHED = "unmatched", "Unmatched"
    AUTO_MATCHED = "auto_matched", "Auto-matched (deterministic rule)"
    NEEDS_REVIEW = "needs_review", "Needs staff review"
    MANUALLY_MATCHED = "manually_matched", "Matched by staff"
    REJECTED = "rejected", "Rejected by staff"


class ListingStatus(models.TextChoices):
    ACTIVE = "active", "Active at the source"
    DELISTED = "delisted", "No longer listed at the source"
    ERROR = "error", "Error"


class PipelineStatus(models.TextChoices):
    DISCOVERED = "DISCOVERED", "Discovered"
    PENDING_REVIEW = "PENDING_REVIEW", "Pending staff review"
    PROCESSING = "PROCESSING", "Processing"
    IMPORTED = "IMPORTED", "Imported"
    TRANSLATION_PENDING = "TRANSLATION_PENDING", "Translation pending"
    PRICING_PENDING = "PRICING_PENDING", "Pricing pending"
    READY_TO_PUBLISH = "READY_TO_PUBLISH", "Ready to publish"
    PUBLISHED = "PUBLISHED", "Published"
    FAILED = "FAILED", "Failed"
    ARCHIVED = "ARCHIVED", "Archived"


class DataOrigin(models.TextChoices):
    FIXTURE = "fixture", "Fixture / mock data (never real source data)"
    LIVE = "live", "Authorised live source"


class ExternalProduct(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    source = models.ForeignKey(ProductSource, on_delete=models.PROTECT, related_name="external_products")
    external_id = models.CharField(max_length=120)
    external_url = models.URLField(max_length=500, blank=True, help_text="Staff reference only; never exposed.")
    language = models.CharField(max_length=5, blank=True, help_text="Language of the source text.")

    raw_title = models.CharField(max_length=500)
    raw_brand = models.CharField(max_length=200, blank=True)
    raw_category_id = models.CharField(max_length=80, blank=True)
    raw_category_path = models.CharField(max_length=500, blank=True)
    raw_description = models.TextField(blank=True)
    raw_attributes = models.JSONField(default=dict, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True, help_text="As received; never edited by staff.")
    payload_hash = models.CharField(max_length=64, blank=True)
    content_hash = models.CharField(
        max_length=64, blank=True, help_text="Text/attribute/image hash; drives translation."
    )
    brand_key = models.CharField(max_length=200, blank=True, db_index=True)
    manufacturer_code = models.CharField(max_length=80, blank=True)
    gender = models.CharField(max_length=10, blank=True, help_text="men | women | kids | unisex | blank")

    canonical_product = models.ForeignKey(
        "catalog.Product", null=True, blank=True, on_delete=models.PROTECT, related_name="external_listings"
    )
    is_primary_listing = models.BooleanField(
        default=False, help_text="The listing whose text created the canonical product (owns its source-language text)."
    )
    match_status = models.CharField(max_length=20, choices=MatchStatus.choices, default=MatchStatus.UNMATCHED)
    match_confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    matched_by = models.CharField(
        max_length=60, blank=True, help_text="rule:T1-gtin, rule:T1-mpn, staff:<public_id>..."
    )
    matched_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=10, choices=ListingStatus.choices, default=ListingStatus.ACTIVE)
    pipeline_status = models.CharField(max_length=20, choices=PipelineStatus.choices, default=PipelineStatus.DISCOVERED)
    pipeline_reason = models.CharField(max_length=200, blank=True)
    error_code = models.CharField(max_length=60, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    data_origin = models.CharField(max_length=10, choices=DataOrigin.choices, default=DataOrigin.FIXTURE)

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    last_imported_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "product_sources_external_product"
        constraints = [models.UniqueConstraint(fields=["source", "external_id"], name="extprod_source_extid_uniq")]
        indexes = [
            models.Index(fields=["source", "pipeline_status"], name="extprod_src_pipeline_idx"),
            models.Index(fields=["match_status"], name="extprod_match_idx"),
            models.Index(fields=["canonical_product"], name="extprod_canonical_idx"),
        ]

    def __str__(self):
        return f"{self.source_id}:{self.external_id}"


class ExternalProductImage(models.Model):
    """Image *metadata* only. Nothing is downloaded here: copying needs ProductSource.allows_image_copy."""

    external_product = models.ForeignKey(ExternalProduct, on_delete=models.CASCADE, related_name="images")
    position = models.PositiveSmallIntegerField(default=0)
    url = models.URLField(max_length=500)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    alt = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "product_sources_external_image"
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["external_product", "position"], name="extimage_prod_pos_uniq")]


class ExternalProductVariant(models.Model):
    external_product = models.ForeignKey(ExternalProduct, on_delete=models.CASCADE, related_name="variants")
    external_variant_id = models.CharField(max_length=120)
    sku = models.CharField(max_length=120, blank=True)
    barcode = models.CharField(max_length=120, blank=True)
    gtin = models.CharField(max_length=14, blank=True, db_index=True)
    gtin_trusted = models.BooleanField(
        default=False, help_text="The source's barcode field is a true GTIN (else the value is only a hint)."
    )
    size_raw = models.CharField(max_length=80, blank=True)
    color_raw = models.CharField(max_length=80, blank=True)
    size_key = models.CharField(max_length=80, blank=True)
    color_key = models.CharField(max_length=80, blank=True)
    attributes = models.JSONField(default=dict, blank=True)
    source_url = models.URLField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    canonical_variant = models.ForeignKey(
        "catalog.ProductVariant", null=True, blank=True, on_delete=models.SET_NULL, related_name="external_variants"
    )
    current_price = models.ForeignKey("SourcePrice", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    current_availability = models.ForeignKey(
        "SourceAvailability", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "product_sources_external_variant"
        constraints = [
            models.UniqueConstraint(fields=["external_product", "external_variant_id"], name="extvariant_prod_id_uniq")
        ]


class _AppendOnly(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise RuntimeError(f"{type(self).__name__} rows are append-only.")
        super().save(*args, **kwargs)


class SourcePrice(_AppendOnly):
    """Source-currency price observation. A row is added only when the value changed (value_hash)."""

    variant = models.ForeignKey(ExternalProductVariant, on_delete=models.CASCADE, related_name="prices")
    list_amount = models.DecimalField(max_digits=14, decimal_places=2)
    sale_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    customer_amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, help_text="Price the source shows a shopper, if given."
    )
    currency_code = models.CharField(max_length=3)
    value_hash = models.CharField(max_length=64)
    observed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "product_sources_source_price"
        indexes = [models.Index(fields=["variant", "observed_at"], name="srcprice_var_obs_idx")]


class SourceAvailability(_AppendOnly):
    variant = models.ForeignKey(ExternalProductVariant, on_delete=models.CASCADE, related_name="availabilities")
    status = models.CharField(max_length=14)  # in_stock | out_of_stock | unknown (same values as the catalogue)
    quantity_hint = models.IntegerField(null=True, blank=True)
    observed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "product_sources_source_availability"
        indexes = [models.Index(fields=["variant", "observed_at"], name="srcavail_var_obs_idx")]


class CandidateStatus(models.TextChoices):
    PENDING = "pending", "Pending review"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class ProductMatchCandidate(models.Model):
    external_product = models.ForeignKey(ExternalProduct, on_delete=models.CASCADE, related_name="match_candidates")
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="match_candidates")
    score = models.DecimalField(max_digits=4, decimal_places=3)
    signals = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=CandidateStatus.choices, default=CandidateStatus.PENDING)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "product_sources_match_candidate"
        constraints = [models.UniqueConstraint(fields=["external_product", "product"], name="matchcand_ext_prod_uniq")]
        ordering = ["-score", "id"]


# Vocabulary mappings: source words -> our canonical rows. Staff-maintained; the importer never guesses.
class ExternalBrandMapping(models.Model):
    source = models.ForeignKey(ProductSource, on_delete=models.CASCADE, related_name="+")
    raw_key = models.CharField(max_length=200, help_text="normalize_search_text(raw brand).")
    brand = models.ForeignKey("brands.Brand", on_delete=models.CASCADE, related_name="+")

    class Meta:
        db_table = "product_sources_brand_mapping"
        constraints = [models.UniqueConstraint(fields=["source", "raw_key"], name="brandmap_src_key_uniq")]


class ExternalCategoryMapping(models.Model):
    source = models.ForeignKey(ProductSource, on_delete=models.CASCADE, related_name="+")
    raw_key = models.CharField(max_length=200, help_text="Source category id, else normalized category path.")
    category = models.ForeignKey("categories.Category", on_delete=models.CASCADE, related_name="+")

    class Meta:
        db_table = "product_sources_category_mapping"
        constraints = [models.UniqueConstraint(fields=["source", "raw_key"], name="catmap_src_key_uniq")]


class ExternalOptionMapping(models.Model):
    """Source size/colour label -> catalogue AttributeOption (EU/UK/TR size labels differ per source)."""

    source = models.ForeignKey(ProductSource, on_delete=models.CASCADE, related_name="+")
    attribute_code = models.CharField(max_length=40, help_text="size | color")
    raw_key = models.CharField(max_length=80)
    option = models.ForeignKey("catalog.AttributeOption", on_delete=models.CASCADE, related_name="+")

    class Meta:
        db_table = "product_sources_option_mapping"
        constraints = [
            models.UniqueConstraint(fields=["source", "attribute_code", "raw_key"], name="optmap_src_attr_key_uniq")
        ]
