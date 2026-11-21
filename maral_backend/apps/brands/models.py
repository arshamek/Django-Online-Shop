import uuid

from django.db import models

from apps.common.languages import LANGUAGE_CHOICES
from apps.common.text import normalize_search_text


class Brand(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    slug = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=160, help_text="Canonical (Latin) brand name.")
    logo = models.FileField(upload_to="brands/", blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "brands_brand"
        ordering = ["name", "id"]
        indexes = [models.Index(fields=["is_active", "name"], name="brand_active_name_idx")]

    def __str__(self):
        return self.name


class BrandTranslation(models.Model):
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="translations")
    language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES)
    name_local = models.CharField(max_length=160)

    class Meta:
        db_table = "brands_brand_translation"
        constraints = [models.UniqueConstraint(fields=["brand", "language"], name="brandtr_brand_lang_uniq")]


class BrandAlias(models.Model):
    """Alternative spellings; feeds search now and source-brand matching in the imports phase."""

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="aliases")
    alias = models.CharField(max_length=160)
    alias_normalised = models.CharField(max_length=160, unique=True, editable=False)

    class Meta:
        db_table = "brands_brand_alias"

    def save(self, *args, **kwargs):
        self.alias_normalised = normalize_search_text(self.alias)
        super().save(*args, **kwargs)
