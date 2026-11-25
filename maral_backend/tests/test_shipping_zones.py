"""Phase 8 shipping: zones, rates, weights, destinations, country policy, address rules, configuration API."""

from decimal import Decimal as D

import pytest
from rest_framework.test import APIClient

from apps.accounts.models import Address
from apps.common.exceptions import BusinessRuleViolation
from apps.shipping import addresses
from apps.shipping.geo import IRAN_PROVINCES, normalize_region
from apps.shipping.models import CountryShippingPolicy, ShippingMethod, ShippingRate, ShippingZone
from apps.shipping.providers.base import ShippingItem, ShippingUnavailable
from apps.shipping.services import methods as shipping
from tests.phase8_helpers import API, make_zone_method

pytestmark = pytest.mark.django_db


def codes(result):
    return [o.code for o in result.options]


# ------------------------------------------------------------------------------------------------ calculation
def test_zone_rate_is_the_price_and_a_range_exists_only_when_configured():
    make_zone_method("intl", base=D("20000000"))
    result = shipping.resolve("DE", D("50000000"))
    option = result.options[0]
    assert (
        option.fee_irr == D("20000000")
        and option.value_status == "estimated"
        and option.kind == "international"
        and option.zone_code == "eu"
    )
    assert option.delivery() == {
        "status": "unknown",
        "min_days": None,
        "max_days": None,
        "unit": "business_days",
    }  # nothing is invented
    ShippingMethod.objects.filter(code="intl").update(delivery_min_days=5, delivery_max_days=9)
    assert shipping.resolve("DE", D(1)).options[0].delivery() == {
        "status": "estimated",
        "min_days": 5,
        "max_days": 9,
        "unit": "business_days",
    }


def test_free_shipping_threshold_and_subtotal_tiers():
    method, zone, rate = make_zone_method("intl", base=D("20000000"))
    rate.max_subtotal_irr, rate.free_over_irr = D("100000000"), None
    rate.save()
    ShippingRate.objects.create(
        method=method,
        zone=zone,
        base_fee_irr=D("5000000"),
        min_subtotal_irr=D("100000000"),
        free_over_irr=D("300000000"),
        sort_order=5,
    )
    assert shipping.quote("intl", "DE", D("50000000")).fee_irr == D("20000000")
    assert shipping.quote("intl", "DE", D("150000000")).fee_irr == D("5000000")
    free = shipping.quote("intl", "DE", D("300000000"))
    assert free.fee_irr == 0 and free.is_free


def test_weight_based_rate_rounds_up_per_started_kilo_and_never_assumes_a_missing_weight():
    make_zone_method("intl", base=D("10000000"), per_kg=D("3000000"))
    ShippingRate.objects.filter(method__code="intl").update(included_weight_grams=500)
    items = [ShippingItem(quantity=2, weight_grams=600)]  # 1200 g -> 700 g over -> 1 started kilo
    assert shipping.quote("intl", "DE", D(1), items=items).fee_irr == D("13000000")
    heavy = [ShippingItem(quantity=1, weight_grams=2600)]  # 2100 over -> 3 kilos
    assert shipping.quote("intl", "DE", D(1), items=heavy).fee_irr == D("19000000")
    unknown = [ShippingItem(quantity=1, weight_grams=None)]
    result = shipping.resolve("DE", D(1), items=unknown)
    assert result.options == [] and result.unavailable == [
        {"method": "intl", "reason": "weight_required"}
    ]  # unavailable, not free
    with pytest.raises(ShippingUnavailable) as exc:
        shipping.quote("intl", "DE", D(1), items=unknown)
    assert exc.value.get_codes() == "shipping_weight_required"


def test_overweight_parcels_are_refused():
    make_zone_method("intl", max_weight_grams=1000)
    assert shipping.resolve("DE", D(1), items=[ShippingItem(quantity=1, weight_grams=900)]).options
    over = shipping.resolve("DE", D(1), items=[ShippingItem(quantity=1, weight_grams=1500)])
    assert over.options == [] and over.unavailable[0]["reason"] == "overweight"


def test_regional_zone_beats_the_country_zone_and_persian_spellings_match():
    method, country_zone, _ = make_zone_method("post", zone_code="ir", countries=("IR",), base=D("9000000"))
    tehran = ShippingZone.objects.create(code="tehran", countries=["IR"], regions=["تهران"], priority=10)
    ShippingRate.objects.create(method=method, zone=tehran, base_fee_irr=D("3000000"))
    for spelling in ("تهران", "Tehran", "استان تهران", "  tehran  "):
        assert shipping.quote("post", "IR", D(1), region=spelling).fee_irr == D("3000000"), spelling
    assert shipping.quote("post", "IR", D(1), region="اصفهان").fee_irr == D("9000000")
    assert shipping.quote("post", "IR", D(1)).fee_irr == D(
        "9000000"
    )  # no region given: the regional zone cannot be proven


def test_iranian_provinces_are_complete_and_normalised():
    assert len(IRAN_PROVINCES) == 31
    assert normalize_region("IR", "خراسان رضوی") == normalize_region("IR", "Razavi Khorasan") == "razavi-khorasan"
    assert normalize_region("IR", "اصفهان") == normalize_region("IR", "Esfahan") == "isfahan"
    assert normalize_region("IR", "يزد") == "yazd"  # Arabic yeh typed instead of Persian yeh
    assert normalize_region("IR", "كرمان") == "kerman"  # Arabic kaf typed instead of Persian kaf
    assert normalize_region("DE", "Bayern") == "bayern" and normalize_region("DE", "") == ""


def test_a_method_with_no_rates_and_no_countries_serves_nowhere_never_for_free():
    ShippingMethod.objects.create(code="empty", names={"en": "x"}, fee_irr=D(0))
    assert shipping.resolve("IR", D(1)).options == []


# ------------------------------------------------------------------------------------------------ unsupported destination / policy
def test_unsupported_destination_has_no_options_and_a_precise_error():
    make_zone_method("intl")
    result = shipping.resolve("KP", D(1))
    assert result.options == [] and not result.destination_served
    with pytest.raises(BusinessRuleViolation) as exc:
        shipping.quote("intl", "KP", D(1))
    assert exc.value.get_codes() == "shipping_method_unavailable"
    assert exc.value.fields == {"destination": ["unsupported_destination"]}


def test_blocked_country_and_restricted_categories():
    make_zone_method("intl")
    CountryShippingPolicy.objects.create(country="DE", is_shippable=False)
    assert shipping.resolve("DE", D(1)).unavailable == [{"method": "*", "reason": "country_not_shippable"}]
    CountryShippingPolicy.objects.filter(country="DE").update(is_shippable=True, restricted_category_slugs=["liquids"])
    blocked = shipping.resolve("DE", D(1), items=[ShippingItem(quantity=1, weight_grams=100, category_slug="liquids")])
    assert blocked.options == [] and blocked.unavailable[0] == {
        "method": "*",
        "reason": "restricted_items",
        "detail": ["liquids"],
    }
    with pytest.raises(ShippingUnavailable) as exc:
        shipping.quote("intl", "DE", D(1), items=[ShippingItem(quantity=1, weight_grams=100, category_slug="liquids")])
    assert exc.value.get_codes() == "shipping_restricted_items"


def test_phase7_flat_methods_keep_working_unchanged():
    ShippingMethod.objects.create(
        code="standard",
        names={"fa": "x"},
        fee_irr=D("4500000"),
        free_over_irr=D("200000000"),
        countries=["IR"],
        value_status="confirmed",
    )
    assert shipping.quote("standard", "ir", D(1)).fee_irr == D("4500000")
    assert shipping.quote("standard", "IR", D("200000000")).is_free
    assert shipping.estimate(D(1)).code == "standard" and shipping.estimate(D(1), "DE") is None


# ------------------------------------------------------------------------------------------------ customer API
def test_estimate_api_for_a_supported_and_an_unsupported_destination():
    make_zone_method(
        "intl", base=D("20000000"), delivery_min_days=5, delivery_max_days=9, delivery_estimate_status="estimated"
    )
    CountryShippingPolicy.objects.create(
        country="DE",
        names={"en": "Germany"},
        customs_mode="on_delivery",
        customs_notice={"en": "Import duties are collected on delivery."},
    )
    client = APIClient()
    body = client.get(f"{API}/shipping/methods", {"country": "de"}).json()
    assert body["supported"] and body["methods"][0]["delivery"] == {
        "status": "estimated",
        "min_days": 5,
        "max_days": 9,
        "unit": "business_days",
    }
    assert (
        body["customs"]["status"] == "payable_on_delivery"
        and "amount_irr" not in body["customs"]
        and "delivery_range_is_an_estimate" in body["disclaimers"]
    )
    assert not any("date" in key for key in body["methods"][0]["delivery"])
    posted = client.post(f"{API}/shipping/estimate", {"country": "KP"}, format="json").json()
    assert posted["supported"] is False and posted["methods"] == [] and posted["customs"]["status"] == "unknown"


def test_countries_and_address_requirements_endpoints():
    make_zone_method("intl")
    CountryShippingPolicy.objects.create(country="FR", is_shippable=False)
    make_zone_method("intl2", zone_code="fr", countries=("FR",))
    client = APIClient()
    assert [c["code"] for c in client.get(f"{API}/shipping/countries").json()["countries"]] == [
        "DE"
    ]  # FR is blocked by policy
    ir = client.get(f"{API}/shipping/address-requirements", {"country": "IR"}).json()
    assert "postal_code" in ir["required_fields"] and len(ir["provinces"]) == 31
    assert (
        "postal_code"
        not in client.get(f"{API}/shipping/address-requirements", {"country": "DE"}).json()["required_fields"]
    )
    assert client.get(f"{API}/shipping/address-requirements").status_code == 422


# ------------------------------------------------------------------------------------------------ address formats
BASE = {
    "recipient_name": "Sara",
    "phone": "+989121234567",
    "country": "IR",
    "city": "Tehran",
    "address_line1": "Valiasr 12",
    "postal_code": "1234567890",
}


def test_persian_digits_and_letters_are_normalised():
    address = addresses.validate(
        {**BASE, "postal_code": "۱۲۳۴۵۶۷۸۹۰", "phone": "+۹۸۹۱۲۱۲۳۴۵۶۷", "address_line1": "كوچه ي ۱۲"}
    )
    assert (
        address["postal_code"] == "1234567890"
        and address["phone"] == "+989121234567"
        and address["address_line1"] == "کوچه ی ۱۲"
    )


@pytest.mark.parametrize("postal", ["", "123", "abcdefghij"])
def test_iranian_postal_code_is_required_and_checked(postal):
    with pytest.raises(BusinessRuleViolation) as exc:
        addresses.validate({**BASE, "postal_code": postal})
    assert exc.value.get_codes() == "address_incomplete" and "postal_code" in exc.value.fields


def test_country_requirements_are_configurable_without_code_changes():
    german = {**BASE, "country": "DE", "postal_code": "", "city": "Berlin"}
    addresses.validate(german)  # default: a postal code is not demanded outside Iran (Phase 7 behaviour)
    CountryShippingPolicy.objects.create(
        country="DE", address_requirements={"required": ["postal_code", "province"], "postal_code_pattern": r"^\d{5}$"}
    )
    with pytest.raises(BusinessRuleViolation):
        addresses.validate(german)
    with pytest.raises(BusinessRuleViolation):
        addresses.validate({**german, "province": "Berlin", "postal_code": "1011"})
    assert addresses.validate({**german, "province": "Berlin", "postal_code": "10115"})["postal_code"] == "10115"
    assert addresses.requirements_for("DE").postal_code_pattern == r"^\d{5}$"


# ------------------------------------------------------------------------------------------------ staff configuration API
def test_shipping_configuration_requires_the_shipping_permission(make_staff, login):
    manager, support = login(make_staff("Order Manager")), login(make_staff("Customer Support"))
    zone = {"code": "eu", "names": {"en": "EU"}, "countries": ["de", "fr"], "priority": 50}
    assert support.post(f"{API}/staff/shipping/zones", zone, format="json").status_code == 403
    created = manager.post(f"{API}/staff/shipping/zones", zone, format="json")
    assert created.status_code == 201 and created.json()["countries"] == ["DE", "FR"]
    assert support.get(f"{API}/staff/shipping/zones").status_code == 200  # reading needs only order access
    method = manager.post(
        f"{API}/staff/shipping/methods",
        {
            "code": "dhl-like",
            "names": {"en": "Courier"},
            "kind": "international",
            "requires_tracking": True,
            "tracking_url_template": "https://track.example/{tracking_number}",
        },
        format="json",
    )
    assert method.status_code == 201, method.content
    rate = manager.post(
        f"{API}/staff/shipping/rates",
        {"method": "dhl-like", "zone": "eu", "base_fee_irr": "20000000", "value_status": "estimated"},
        format="json",
    )
    assert rate.status_code == 201, rate.content
    assert shipping.resolve("FR", D(1)).options[0].code == "dhl-like"
    assert (
        manager.patch(f"{API}/staff/shipping/methods/dhl-like", {"is_active": False}, format="json").status_code == 200
    )
    assert shipping.resolve("FR", D(1)).options == []
    assert manager.delete(f"{API}/staff/shipping/zones/{created.json()['id']}").status_code == 405  # deactivate instead


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("methods", {"code": "m", "names": {}, "tracking_url_template": "http://insecure/{tracking_number}"}),
        ("methods", {"code": "m", "names": {}, "tracking_url_template": "https://no-placeholder.example/"}),
        ("methods", {"code": "m", "names": {}, "provider_key": "does-not-exist"}),
        ("methods", {"code": "m", "names": {}, "delivery_min_days": 9, "delivery_max_days": 3}),
        ("zones", {"code": "z", "countries": ["Germany"]}),
        ("country-policies", {"country": "DE", "address_requirements": {"postal_code_pattern": "("}}),
        ("country-policies", {"country": "DE", "address_requirements": {"required": ["shoe_size"]}}),
    ],
)
def test_configuration_is_validated(make_staff, login, path, payload):
    assert login(make_staff("Order Manager")).post(
        f"{API}/staff/shipping/{path}", payload, format="json"
    ).status_code in (400, 422)


def test_address_model_import_is_unchanged():
    assert Address._meta.get_field("delivery_instructions")  # delivery instructions are part of every address format
