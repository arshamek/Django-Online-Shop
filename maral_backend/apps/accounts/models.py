"""
Custom user model (must exist before the first migration - Django cannot swap it later).

Phase 2 scope is deliberately minimal: the model needed so migrations are safe. Profiles,
addresses, OTP login and guest sessions belong to the `accounts` phase and depend on the
open authentication decision D-6.
"""

import uuid

from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.db import models


class UserManager(DjangoUserManager):
    def _create_user(self, username, email, password, **extra_fields):
        # Django normalises a missing email to "" - store NULL instead so the filtered
        # unique index on `email` (NULLs allowed many times) is not violated by "" duplicates.
        return super()._create_user(username, email or None, password, **extra_fields)


class User(AbstractUser):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    email = models.EmailField("email address", null=True, blank=True)
    phone = models.CharField(max_length=32, null=True, blank=True)

    objects = UserManager()

    class Meta:
        db_table = "accounts_user"
        constraints = [
            # Nullable-unique columns become *filtered* unique indexes (WHERE ... IS NOT NULL) on
            # SQL Server; a plain UNIQUE would allow only one NULL. See docs/setup/DATABASE.md.
            models.UniqueConstraint(
                fields=["email"], condition=models.Q(email__isnull=False), name="uniq_user_email_not_null"
            ),
            models.UniqueConstraint(
                fields=["phone"], condition=models.Q(phone__isnull=False), name="uniq_user_phone_not_null"
            ),
        ]

    def save(self, *args, **kwargs):
        self.email = self.email or None
        self.phone = self.phone or None
        super().save(*args, **kwargs)
