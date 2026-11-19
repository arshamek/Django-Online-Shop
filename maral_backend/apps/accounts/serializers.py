import re

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.audit.models import AuditLog

from . import validators
from .models import Address, Currency, Language, User, UserSession
from .normalizers import normalize_email, normalize_phone, normalize_text, to_ascii_digits
from .roles import roles_for
from .services.auth import password_error_messages

PASSWORD_MAX = 128


def _password_field(**kwargs):
    return serializers.CharField(
        write_only=True, max_length=PASSWORD_MAX, trim_whitespace=False, style={"input_type": "password"}, **kwargs
    )


# ---- output -------------------------------------------------------------------------------
class ProfileSerializer(serializers.Serializer):
    avatar_url = serializers.SerializerMethodField(help_text="Absolute URL or null.")
    birth_date = serializers.DateField(allow_null=True)
    language = serializers.ChoiceField(choices=Language.choices)
    currency = serializers.ChoiceField(choices=Currency.choices)

    def get_avatar_url(self, profile) -> str | None:
        if not profile.avatar:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(profile.avatar.url) if request else profile.avatar.url


class UserSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    full_name = serializers.CharField(source="get_full_name", read_only=True)
    email_verified = serializers.BooleanField(read_only=True)
    phone_verified = serializers.BooleanField(read_only=True)
    roles = serializers.SerializerMethodField(help_text="Staff roles; empty for customers.")
    permissions = serializers.SerializerMethodField(help_text="Staff permission codenames (for UI gating only).")
    profile = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "phone",
            "first_name",
            "last_name",
            "full_name",
            "status",
            "email_verified",
            "phone_verified",
            "is_staff",
            "roles",
            "permissions",
            "created_at",
            "last_login",
            "profile",
        ]
        read_only_fields = fields

    def get_roles(self, user) -> list[str]:
        return roles_for(user) if user.is_staff else []

    def get_permissions(self, user) -> list[str]:
        if not user.is_staff:
            return []
        return sorted(p.split(".", 1)[1] for p in user.get_all_permissions() if p.startswith("accounts."))

    def get_profile(self, user) -> dict:
        from .services.profile import get_profile

        return ProfileSerializer(get_profile(user), context=self.context).data


class AuthResponseSerializer(serializers.Serializer):
    user = UserSerializer()
    csrf_token = serializers.CharField(
        help_text="Send as `X-CSRFToken` on unsafe requests. Changes after login/refresh."
    )


class CSRFSerializer(serializers.Serializer):
    csrf_token = serializers.CharField()


class RefreshResponseSerializer(serializers.Serializer):
    csrf_token = serializers.CharField()
    expires_at = serializers.DateTimeField(help_text="When the session cookie expires unless refreshed again.")


class SessionSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    ip_address = serializers.SerializerMethodField(help_text="Last part masked.")
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = UserSession
        fields = ["id", "created_at", "last_seen_at", "ip_address", "user_agent", "is_current"]
        read_only_fields = fields

    def get_ip_address(self, session) -> str | None:
        ip = session.ip_address
        if not ip:
            return None
        if ":" in ip:
            return ":".join(ip.split(":")[:3]) + ":*"
        return ".".join(ip.split(".")[:3]) + ".*"

    def get_is_current(self, session) -> bool:
        current = self.context.get("current_session")
        return current is not None and current.pk == session.pk


# ---- input --------------------------------------------------------------------------------
class RegisterSerializer(serializers.Serializer):
    email = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=254)
    phone = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=32,
        help_text="Iranian (09121234567) or international (+90...) format; Persian digits accepted.",
    )
    password = _password_field()
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    language = serializers.ChoiceField(choices=Language.choices, default=Language.FA)

    def validate_email(self, value):
        try:
            return normalize_email(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError("Enter a valid email address.") from exc

    def validate_phone(self, value):
        try:
            return normalize_phone(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate(self, attrs):
        if not attrs.get("email") and not attrs.get("phone"):
            raise serializers.ValidationError(
                {"email": ["Provide an email or a phone number."], "phone": ["Provide an email or a phone number."]}
            )
        candidate = User(
            email=attrs.get("email"),
            phone=attrs.get("phone"),
            first_name=attrs.get("first_name", ""),
            last_name=attrs.get("last_name", ""),
        )
        errors = password_error_messages(attrs["password"], candidate)
        if errors:
            raise serializers.ValidationError({"password": errors})
        attrs["first_name"] = normalize_text(attrs.get("first_name"))
        attrs["last_name"] = normalize_text(attrs.get("last_name"))
        return attrs


class LoginSerializer(serializers.Serializer):
    identifier = serializers.CharField(max_length=254, help_text="Email address or phone number.")
    password = _password_field()


class PasswordChangeSerializer(serializers.Serializer):
    current_password = _password_field()
    new_password = _password_field()


class PasswordResetRequestSerializer(serializers.Serializer):
    identifier = serializers.CharField(max_length=254, help_text="The account email address.")


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField(max_length=64)
    token = serializers.CharField(max_length=128)
    new_password = _password_field()


class TokenSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=2048)


class CodeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=12)

    def validate_code(self, value):
        value = to_ascii_digits(value).strip()
        if not re.fullmatch(r"\d{6}", value):
            raise serializers.ValidationError("Enter the 6-digit code.")
        return value


class EmailChangeSerializer(serializers.Serializer):
    email = serializers.CharField(max_length=254)
    current_password = _password_field()

    def validate_email(self, value):
        try:
            email = normalize_email(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError("Enter a valid email address.") from exc
        if not email:
            raise serializers.ValidationError("This field may not be blank.")
        return email


class PhoneChangeSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=32)
    current_password = _password_field()

    def validate_phone(self, value):
        try:
            phone = normalize_phone(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        if not phone:
            raise serializers.ValidationError("This field may not be blank.")
        return phone


class ProfileUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=150)
    birth_date = serializers.DateField(required=False, allow_null=True)
    language = serializers.ChoiceField(choices=Language.choices, required=False)
    currency = serializers.ChoiceField(choices=Currency.choices, required=False)

    def validate_birth_date(self, value):
        from django.utils import timezone

        if value and value > timezone.localdate():
            raise serializers.ValidationError("Birth date cannot be in the future.")
        return value


class AvatarUploadSerializer(serializers.Serializer):
    avatar = serializers.FileField()


class AddressSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    postal_code = serializers.CharField(required=False, allow_blank=True, max_length=20)

    class Meta:
        model = Address
        fields = [
            "id",
            "label",
            "recipient_name",
            "phone",
            "country",
            "province",
            "city",
            "district",
            "address_line1",
            "address_line2",
            "postal_code",
            "delivery_instructions",
            "is_default",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {
            "label": {"required": False},
            "province": {"required": False},
            "district": {"required": False},
            "address_line2": {"required": False},
            "delivery_instructions": {"required": False},
            "is_default": {"required": False},
        }

    def validate_phone(self, value):
        try:
            phone = normalize_phone(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        if not phone:
            raise serializers.ValidationError("This field may not be blank.")
        return phone

    def validate_country(self, value):
        try:
            return validators.validate_country_code(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages[0]) from exc

    def validate(self, attrs):
        for field in (
            "label",
            "recipient_name",
            "province",
            "city",
            "district",
            "address_line1",
            "address_line2",
            "delivery_instructions",
        ):
            if field in attrs:
                attrs[field] = normalize_text(attrs[field])
        for field in ("recipient_name", "city", "address_line1"):
            if field in attrs and not attrs[field]:
                raise serializers.ValidationError({field: ["This field may not be blank."]})
        country = attrs.get("country") or (self.instance.country if self.instance else None)
        if "postal_code" in attrs or "country" in attrs or self.instance is None:
            raw = attrs.get("postal_code", self.instance.postal_code if self.instance else "")
            try:
                attrs["postal_code"] = validators.normalize_postal_code(country, raw)
            except DjangoValidationError as exc:
                raise serializers.ValidationError({"postal_code": [exc.messages[0]]}) from exc
        return attrs


class StaffCustomerSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    email_verified = serializers.BooleanField(read_only=True)
    phone_verified = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "phone",
            "first_name",
            "last_name",
            "status",
            "email_verified",
            "phone_verified",
            "created_at",
            "last_login",
        ]
        read_only_fields = fields


class StaffCustomerDetailSerializer(StaffCustomerSerializer):
    address_count = serializers.SerializerMethodField()
    active_session_count = serializers.SerializerMethodField()
    locked_until = serializers.DateTimeField(read_only=True)
    failed_login_count = serializers.IntegerField(read_only=True)

    class Meta(StaffCustomerSerializer.Meta):
        fields = [
            *StaffCustomerSerializer.Meta.fields,
            "locked_until",
            "failed_login_count",
            "address_count",
            "active_session_count",
        ]
        read_only_fields = fields

    def get_address_count(self, user) -> int:
        return user.addresses.count()

    def get_active_session_count(self, user) -> int:
        return user.auth_sessions.filter(revoked_at__isnull=True).count()


class SuspendSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=300)


class AuditLogSerializer(serializers.ModelSerializer):
    actor_id = serializers.UUIDField(source="actor_public_id", read_only=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "created_at",
            "action",
            "actor_id",
            "target_type",
            "target_id",
            "ip_address",
            "request_id",
            "metadata",
        ]
        read_only_fields = fields
