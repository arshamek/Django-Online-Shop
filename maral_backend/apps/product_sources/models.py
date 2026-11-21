from django.db import models


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
