import uuid

from django.db import models

from apps.common.languages import LANGUAGE_CHOICES


class Category(models.Model):
    """Adjacency tree + materialised `path` ("/1/7/") so subtree filters are a prefix seek."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    slug = models.SlugField(max_length=80, unique=True)
    path = models.CharField(max_length=255, blank=True, editable=False)
    depth = models.PositiveSmallIntegerField(default=0, editable=False)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    image = models.FileField(upload_to="categories/", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "categories_category"
        verbose_name_plural = "categories"
        ordering = ["sort_order", "id"]
        indexes = [
            models.Index(fields=["path"], name="category_path_idx"),
            models.Index(fields=["parent", "sort_order"], name="category_parent_sort_idx"),
        ]

    def __str__(self):
        return self.slug

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self._sync_path()

    def _sync_path(self):
        parent_path = self.parent.path if self.parent_id else "/"
        path = f"{parent_path}{self.pk}/"
        depth = path.count("/") - 2  # "/1/" -> 0, "/1/7/" -> 1
        if (self.path, self.depth) != (path, depth):
            self.path, self.depth = path, depth
            Category.objects.filter(pk=self.pk).update(path=path, depth=depth)
            for child in Category.objects.filter(parent=self):  # subtree move: rebuild below
                child.parent = self
                child._sync_path()


class CategoryTranslation(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="translations")
    language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "categories_category_translation"
        constraints = [models.UniqueConstraint(fields=["category", "language"], name="categorytr_cat_lang_uniq")]

    def __str__(self):
        return f"{self.category_id}:{self.language}"
