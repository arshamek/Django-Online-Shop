import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.catalog.models import AttributeOption, FulfillmentType, Product, ProductVariant, VariantPrice
from apps.catalog.services.variants import compute_signature
from apps.common.text import normalize_search_text

from . import catalog_factories as f

pytestmark = pytest.mark.django_db


def test_proxy_purchase_cannot_use_marals_own_source():
    variant = ProductVariant(fulfillment_type=FulfillmentType.PROXY_PURCHASE, source=f.source("maral"))
    with pytest.raises(ValidationError):
        variant.clean()


def test_direct_inventory_keeps_its_origin_source_for_the_badge():
    ProductVariant(fulfillment_type=FulfillmentType.DIRECT_INVENTORY, source=f.source("trendyol")).clean()
    ProductVariant(fulfillment_type=FulfillmentType.DIRECT_INVENTORY, source=f.source("maral")).clean()


@pytest.mark.parametrize("slug", ["search", "filters", "related", "recently-viewed"])
def test_reserved_slugs_are_rejected(slug):
    with pytest.raises(ValidationError):
        Product(slug=slug, brand=f.brand(), category=f.category()).save()


def test_option_codes_are_stored_lower_case_and_unique_per_attribute():
    size, _ = f.axes()
    AttributeOption.objects.create(attribute=size, code="XL")
    assert AttributeOption.objects.get(attribute=size).code == "xl"
    with pytest.raises(IntegrityError), transaction.atomic():
        AttributeOption.objects.create(attribute=size, code="xl")


def test_final_price_can_never_exceed_list_price():
    variant = f.add_variant(f.product("p", variants=[]), price=False)
    with pytest.raises(IntegrityError), transaction.atomic():
        VariantPrice.objects.create(
            variant=variant, original_amount=1, original_currency="TRY", list_irr=100, final_irr=101
        )


def test_variant_signature_is_unique_per_product_and_order_independent():
    obj = f.product("p", variants=[{"size": "m", "color": "black"}])
    variant = obj.variants.get()
    assert variant.option_signature == compute_signature([o.option_id for o in variant.options.all()])
    assert compute_signature([2, 1]) == compute_signature([1, 2])
    other = f.add_variant(obj, size="l", color="black")
    other.option_signature = variant.option_signature
    with pytest.raises(IntegrityError), transaction.atomic():
        other.save()


def test_search_normalisation_unifies_persian_and_arabic_forms():
    assert normalize_search_text("كتاني  ۱۲۳") == "کتانی 123"
    assert normalize_search_text("می‌خواهم") == "می خواهم"  # ZWNJ -> space
    assert normalize_search_text("  Belted COAT ") == "belted coat"
    assert normalize_search_text(None) == ""


def test_translation_search_text_is_maintained_on_save():
    obj = f.product("p", names={"fa": "كتاني سفيد"})
    assert obj.translations.get().search_text == "کتانی سفید"


def test_delivery_rules_are_unique_per_type_and_source_including_the_generic_rule():
    from apps.catalog.models import DeliveryEstimateRule

    kwargs = {"fulfillment_type": FulfillmentType.PROXY_PURCHASE, "min_days": 1, "max_days": 2}
    DeliveryEstimateRule.objects.create(**kwargs)
    with pytest.raises(IntegrityError), transaction.atomic():
        DeliveryEstimateRule.objects.create(**kwargs)  # a second generic rule
    namshi = f.source("namshi")
    DeliveryEstimateRule.objects.create(source=namshi, **kwargs)
    with pytest.raises(IntegrityError), transaction.atomic():
        DeliveryEstimateRule.objects.create(source=namshi, **kwargs)
    with pytest.raises(IntegrityError), transaction.atomic():
        DeliveryEstimateRule.objects.create(fulfillment_type=FulfillmentType.DIRECT_INVENTORY, min_days=5, max_days=2)
