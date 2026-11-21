"""
Reviews are returned by the storefront ONLY when a moderator published them AND a publication right is
recorded (customer consent or a licence). Open question Q-reviews (docs/frontend-analysis/FRONTEND_GAPS.md):
how reviews are collected is a later phase, so this phase has no submission endpoint.
"""

import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.common.languages import LANGUAGE_CHOICES


class ReviewStatus(models.TextChoices):
    PENDING = "pending", "Pending moderation"
    PUBLISHED = "published", "Published"
    REJECTED = "rejected", "Rejected"


class PublicationRights(models.TextChoices):
    NONE = "none", "No publication right recorded"
    CUSTOMER_CONSENT = "customer_consent", "Customer consented to publication"
    LICENSED = "licensed", "Licensed from the author / source"


class ReviewQuerySet(models.QuerySet):
    def publishable(self):
        return self.filter(status=ReviewStatus.PUBLISHED).exclude(publication_rights=PublicationRights.NONE)


class Review(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    product = models.ForeignKey("catalog.Product", on_delete=models.CASCADE, related_name="reviews")
    author_name = models.CharField(max_length=80)
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    title = models.CharField(max_length=160, blank=True)
    body = models.TextField(blank=True)
    language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES, default="fa")
    verified_purchase = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=ReviewStatus.choices, default=ReviewStatus.PENDING)
    publication_rights = models.CharField(
        max_length=20, choices=PublicationRights.choices, default=PublicationRights.NONE
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ReviewQuerySet.as_manager()

    class Meta:
        db_table = "reviews_review"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["product", "status", "-created_at"], name="review_prod_status_idx")]
        constraints = [
            models.CheckConstraint(condition=models.Q(rating__gte=1, rating__lte=5), name="review_rating_1_5")
        ]
