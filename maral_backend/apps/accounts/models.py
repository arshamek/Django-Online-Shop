"""
Account domain models.

Identifiers (`email`, `phone`) are stored in canonical form (see normalizers.py) and are unique
when present, enforced by *filtered* unique indexes (WHERE ... IS NOT NULL) so any number of
accounts may lack one of them. At least one identifier must exist (CHECK constraint).
"""

import uuid

from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

from .normalizers import normalize_email, normalize_phone, normalize_text


class AccountStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    SUSPENDED = "suspended", "Suspended"  # set by staff; cannot sign in
    DEACTIVATED = "deactivated", "Deactivated"  # closed (by the customer or staff); cannot sign in


class Language(models.TextChoices):
    FA = "fa", "فارسی"
    EN = "en", "English"
    AR = "ar", "العربية"
    TR = "tr", "Türkçe"


class Currency(models.TextChoices):
    IRT = "IRT", "Toman"
    IRR = "IRR", "Rial"
    TRY = "TRY", "Turkish lira"
    USD = "USD", "US dollar"
    EUR = "EUR", "Euro"
    AED = "AED", "UAE dirham"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, email):
        return self.get(email=normalize_email(email))

    def create_user(self, email=None, phone=None, password=None, **extra_fields):
        email, phone = normalize_email(email), normalize_phone(phone)
        if not email and not phone:
            raise ValueError("An account needs an email or a phone number.")
        user = self.model(email=email, phone=phone, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.update(is_staff=True, is_superuser=True, email_verified_at=timezone.now())
        if not email:
            raise ValueError("Superusers need an email address.")
        return self.create_user(email=email, password=password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    email = models.EmailField(max_length=254, null=True, blank=True)
    phone = models.CharField(max_length=20, null=True, blank=True, help_text="E.164, e.g. +989121234567")
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=16, choices=AccountStatus.choices, default=AccountStatus.ACTIVE)
    is_staff = models.BooleanField(default=False, help_text="May use the staff API / admin (still needs permissions).")
    email_verified_at = models.DateTimeField(null=True, blank=True)
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    failed_login_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # `last_login` comes from AbstractBaseUser.

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        db_table = "accounts_user"
        constraints = [
            models.UniqueConstraint(
                fields=["email"], condition=models.Q(email__isnull=False), name="uniq_user_email_not_null"
            ),
            models.UniqueConstraint(
                fields=["phone"], condition=models.Q(phone__isnull=False), name="uniq_user_phone_not_null"
            ),
            models.CheckConstraint(
                condition=models.Q(email__isnull=False) | models.Q(phone__isnull=False),
                name="user_has_email_or_phone",
            ),
        ]
        indexes = [models.Index(fields=["status"], name="user_status_idx")]

    def __str__(self):
        return self.email or self.phone or str(self.public_id)

    @property
    def is_active(self) -> bool:  # Django's auth machinery reads this
        return self.status == AccountStatus.ACTIVE

    @property
    def email_verified(self) -> bool:
        return self.email is not None and self.email_verified_at is not None

    @property
    def phone_verified(self) -> bool:
        return self.phone is not None and self.phone_verified_at is not None

    @property
    def is_locked(self) -> bool:
        return self.locked_until is not None and self.locked_until > timezone.now()

    def get_full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self) -> str:
        return self.first_name

    def clean(self):
        super().clean()
        self.email = normalize_email(self.email)
        self.phone = normalize_phone(self.phone)

    def save(self, *args, **kwargs):
        # Normalise on every write path (API, admin, shell) so uniqueness is on the canonical form.
        self.email = normalize_email(self.email)
        self.phone = normalize_phone(self.phone)
        self.first_name = normalize_text(self.first_name)
        self.last_name = normalize_text(self.last_name)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "updated_at" not in update_fields:
            kwargs["update_fields"] = [*update_fields, "updated_at"]
        super().save(*args, **kwargs)


class CustomerProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    language = models.CharField(max_length=2, choices=Language.choices, default=Language.FA)
    currency = models.CharField(max_length=3, choices=Currency.choices, default=Currency.IRT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_customer_profile"


class Address(models.Model):
    """A delivery address. Supports Iranian and international formats (see validators.py)."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="addresses")
    label = models.CharField(max_length=50, blank=True)
    recipient_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, help_text="E.164 contact number for the courier")
    country = models.CharField(max_length=2, help_text="ISO 3166-1 alpha-2")
    province = models.CharField(max_length=100, blank=True, help_text="Province / state / region (استان)")
    city = models.CharField(max_length=100)
    district = models.CharField(max_length=100, blank=True, help_text="District / neighbourhood (محله)")
    address_line1 = models.CharField(max_length=255, help_text="Street, building number")
    address_line2 = models.CharField(max_length=255, blank=True, help_text="Unit, floor, landmark")
    postal_code = models.CharField(max_length=20, blank=True)
    delivery_instructions = models.CharField(max_length=500, blank=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "accounts_address"
        ordering = ["-is_default", "-created_at", "-id"]
        constraints = [
            # At most one default address per user (filtered unique index on SQL Server).
            models.UniqueConstraint(
                fields=["user"], condition=models.Q(is_default=True), name="uniq_default_address_per_user"
            ),
        ]
        indexes = [models.Index(fields=["user", "-created_at"], name="address_user_created_idx")]


class SessionEnd(models.TextChoices):
    LOGOUT = "logout", "Logout"
    USER = "user", "Revoked by user"
    PASSWORD_CHANGE = "password_change", "Password changed"
    PASSWORD_RESET = "password_reset", "Password reset"
    STAFF = "staff", "Revoked by staff"
    EXPIRED = "expired", "Expired"


class UserSession(models.Model):
    """
    Server-side record of a signed-in browser. Revoking it (revoked_at) invalidates the cookie
    immediately, independent of the Django session row's own expiry.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="auth_sessions")
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(default=timezone.now)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=20, choices=SessionEnd.choices, blank=True)

    class Meta:
        db_table = "accounts_user_session"
        ordering = ["-last_seen_at"]
        indexes = [models.Index(fields=["user", "revoked_at"], name="usersession_user_active_idx")]


class PhoneVerificationCode(models.Model):
    """One-time code proving control of a phone number. Only a keyed hash of the code is stored."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="phone_codes")
    phone = models.CharField(max_length=20)
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "accounts_phone_verification_code"
        indexes = [models.Index(fields=["phone", "created_at"], name="phonecode_phone_created_idx")]


class StaffPermissions(models.Model):
    """
    Permission anchor (no table): declares the staff capabilities that roles are built from
    (see roles.py). Business apps added later may define their own model permissions and
    extend the role table.
    """

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("customers_view", "Can view customer accounts"),
            ("customers_suspend", "Can suspend / reactivate customer accounts"),
            ("customers_unlock", "Can unlock customer accounts after failed-login lockout"),
            ("auditlog_view", "Can view the audit log"),
            ("catalog_manage", "Can manage the product catalogue"),
            ("imports_manage", "Can run and monitor product imports"),
            ("imports_review_matches", "Can approve / reject product matches"),
            ("pricing_manage", "Can manage pricing rules and currency rates"),
            ("payments_view", "Can view payments"),
            ("refunds_manage", "Can create and approve refunds"),
            ("orders_view", "Can view orders"),
            ("orders_manage", "Can change order status and procurement"),
        ]
