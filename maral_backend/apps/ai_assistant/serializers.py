from rest_framework import serializers

from .constants import ALL_SOURCES, EXTERNAL_SOURCES, SUPPORTED_LANGUAGES
from .models import AssistantConfig, Conversation, Message, PurchaseInquiry

LANG_CHOICES = [*SUPPORTED_LANGUAGES, "auto"]


class ProductRefSerializer(serializers.Serializer):
    source = serializers.ChoiceField(choices=ALL_SOURCES)
    id = serializers.UUIDField()


class BudgetSerializer(serializers.Serializer):
    """Whole Toman (customer-facing currency)."""

    min = serializers.IntegerField(min_value=0, required=False, allow_null=True, max_value=10**12)
    max = serializers.IntegerField(min_value=0, required=False, allow_null=True, max_value=10**12)

    def validate(self, attrs):
        if attrs.get("min") is not None and attrs.get("max") is not None and attrs["min"] > attrs["max"]:
            raise serializers.ValidationError("min must not exceed max.")
        return attrs


class FiltersSerializer(serializers.Serializer):
    colors = serializers.ListField(child=serializers.CharField(max_length=30), required=False, max_length=5)
    sizes = serializers.ListField(child=serializers.CharField(max_length=10), required=False, max_length=5)
    min_price = serializers.IntegerField(min_value=0, required=False, allow_null=True, max_value=10**12)
    max_price = serializers.IntegerField(min_value=0, required=False, allow_null=True, max_value=10**12)


class ChatRequestSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=4000, trim_whitespace=True)
    language = serializers.ChoiceField(choices=LANG_CHOICES, required=False)
    conversation_id = serializers.UUIDField(required=False)
    budget = BudgetSerializer(required=False)
    filters = FiltersSerializer(required=False)
    preferred_sources = serializers.ListField(
        child=serializers.ChoiceField(choices=ALL_SOURCES), required=False, max_length=3
    )
    product_references = ProductRefSerializer(many=True, required=False, max_length=5)


class RecommendationRequestSerializer(serializers.Serializer):
    query = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    language = serializers.ChoiceField(choices=LANG_CHOICES, required=False)
    budget = BudgetSerializer(required=False)
    filters = FiltersSerializer(required=False)
    preferred_sources = serializers.ListField(
        child=serializers.ChoiceField(choices=ALL_SOURCES), required=False, max_length=3
    )

    def validate(self, attrs):
        if not (attrs.get("query") or "").strip():
            raise serializers.ValidationError({"query": "Describe the product you are looking for."})
        return attrs


class CompareRequestSerializer(serializers.Serializer):
    product_references = ProductRefSerializer(many=True, min_length=2, max_length=4)
    language = serializers.ChoiceField(choices=LANG_CHOICES, required=False)


class InquiryCreateSerializer(serializers.Serializer):
    product_ref = ProductRefSerializer()
    size = serializers.CharField(max_length=80, required=False, allow_blank=True, default="")
    color = serializers.CharField(max_length=80, required=False, allow_blank=True, default="")
    quantity = serializers.IntegerField(min_value=1, max_value=10, default=1)
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    conversation_id = serializers.UUIDField(required=False)

    def validate_product_ref(self, value):
        if value["source"] not in EXTERNAL_SOURCES:
            raise serializers.ValidationError(
                "Only Trendyol or Namshi products need a purchase inquiry; add Maral products to the cart."
            )
        return value


def _toman(irr):
    return None if irr is None else str(int(irr / 10))


class InquirySerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    source_code = serializers.CharField(source="source.code", read_only=True)
    prices = serializers.SerializerMethodField()
    is_order = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseInquiry
        fields = [
            "id",
            "source_code",
            "title",
            "brand",
            "size",
            "color",
            "quantity",
            "customer_note",
            "status",
            "prices",
            "snapshot_observed_at",
            "availability_seen",
            "created_at",
            "updated_at",
            "is_order",
        ]

    def get_prices(self, obj):
        return {
            "original": {
                "amount": format(obj.original_amount.normalize(), "f") if obj.original_amount is not None else None,
                "currency": obj.original_currency or None,
            },
            "estimated_maral": {
                "amount": _toman(obj.estimated_price_irr),
                "currency": "IRT",
                "status": obj.estimated_price_status or None,
                "rule_version": obj.price_rule_version,
            },
            "confirmed": None,
        }

    def get_is_order(self, obj):
        return False


class StaffInquirySerializer(InquirySerializer):
    customer_id = serializers.SerializerMethodField()
    source_url = serializers.CharField(read_only=True)

    class Meta(InquirySerializer.Meta):
        fields = InquirySerializer.Meta.fields + [
            "external_id",
            "source_url",
            "customer_id",
            "price_checked_at",
            "availability_checked_at",
            "feasibility_checked_at",
            "staff_notes",
            "handled_by",
        ]

    def get_customer_id(self, obj):
        return str(getattr(obj.user, "public_id", obj.user_id))


class StaffInquiryUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["under_review", "feasible", "not_feasible", "closed"], required=False)
    price_checked = serializers.BooleanField(required=False)
    availability_checked = serializers.BooleanField(required=False)
    feasibility_checked = serializers.BooleanField(required=False)
    staff_notes = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class ConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssistantConfig
        exclude = [
            "id",
            "updated_by",
            "updated_at",
            "last_provider_test_at",
            "last_provider_test_ok",
            "last_provider_test_error",
        ]

    def validate_supported_languages(self, value):
        if not value or not isinstance(value, list) or any(v not in SUPPORTED_LANGUAGES for v in value):
            raise serializers.ValidationError(f"Use a non-empty subset of {list(SUPPORTED_LANGUAGES)}.")
        return list(dict.fromkeys(value))

    def validate_enabled_sources(self, value):
        if not isinstance(value, list) or any(v not in ALL_SOURCES for v in value):
            raise serializers.ValidationError(f"Use a subset of {list(ALL_SOURCES)}.")
        return list(dict.fromkeys(value))

    def validate_model(self, value):
        return value.strip()


class ConversationListSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(source="public_id", read_only=True)
    message_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Conversation
        fields = ["id", "title", "language", "created_at", "updated_at", "message_count"]


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ["id", "role", "content", "language", "data", "demo_mode", "created_at"]
