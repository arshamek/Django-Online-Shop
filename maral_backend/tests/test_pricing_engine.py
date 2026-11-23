"""Part G: price calculation. Expected numbers come from hand calculation (tests/fixtures/pricing_scenarios.json)."""

from decimal import Decimal as D
from pathlib import Path

import pytest

from apps.catalog.context import to_irr
from apps.pricing.models import PricingRule
from apps.pricing.services.engine import PriceInput, calculate
from tests import catalog_factories as f
from tests.pricing_helpers import SCENARIOS, fx, make_ruleset, rate, route, scenario_ruleset

pytestmark = pytest.mark.django_db


def run(scenario, **extra):
    rs = scenario_ruleset(scenario)
    inp = PriceInput(
        **{k: (D(v) if k == "original_amount" else v) for k, v in scenario["input"].items()},
        presentation_currency="IRR",
        **extra,
    )
    return calculate(inp, rs)


@pytest.mark.parametrize("scenario", SCENARIOS["scenarios"], ids=lambda s: s["id"])
def test_hand_calculated_scenarios(scenario):
    fx()
    calc = run(scenario)
    got = calc.as_dict()
    flat = {
        "item_irr": got["item_irr"],
        "profit_irr": got["profit"]["profit_irr"],
        "price_before_rounding_irr": got["price_before_rounding_irr"],
        "rounding_adjustment_irr": got["rounding_adjustment_irr"],
        "unit_price_irr": got["unit_price_irr"],
        "unit_separate_irr": got["unit_separate_irr"],
        "unit_total_irr": got["unit_total_irr"],
        "internal_cost_irr": got["internal_cost_irr"],
        "realized_profit_irr": got["realized_profit_irr"],
        "realized_markup_percent": got["realized_markup_percent"],
        "realized_margin_percent": got["realized_margin_percent"],
        "min_profit_applied": got["profit"]["min_profit_applied"],
        "publishable": got["publishable"],
    }
    for key, expected in scenario["expect"].items():
        value = flat[key]
        assert (D(str(value)) == D(str(expected))) if isinstance(expected, str) else value == expected, (
            scenario["id"],
            key,
            value,
            expected,
        )
    assert calc.blockers == []


# ---------------------------------------------------------------------------------------- currency -> Toman
@pytest.mark.parametrize(
    ("code", "toman"), [("TRY", "1500000"), ("AED", "15000000"), ("USD", "55000000"), ("EUR", "60000000")]
)
def test_source_currency_to_toman(code, toman):
    fx()
    rs = make_ruleset(target_profit_percent="0")
    calc = calculate(PriceInput(original_amount=D("1000"), original_currency=code, presentation_currency="IRT"), rs)
    assert calc.item_irr == D(toman) * 10
    assert calc.unit_display == D(toman)  # Toman is exactly IRR / 10
    assert calc.presentation_currency == "IRT"


def test_rial_versus_toman_are_the_same_money():
    fx()
    rs = make_ruleset(target_profit_percent="0")
    irr = calculate(PriceInput(original_amount=D("1000"), original_currency="TRY", presentation_currency="IRR"), rs)
    irt = calculate(PriceInput(original_amount=D("1000"), original_currency="TRY", presentation_currency="IRT"), rs)
    assert irr.unit_price_irr == irt.unit_price_irr == D("15000000")
    assert irr.unit_display == D("15000000") and irt.unit_display == D("1500000")
    assert to_irr(irt.unit_display, "IRT") == irr.unit_display  # catalogue helper agrees


def test_provider_quoting_in_toman_is_normalised_to_rial():
    from apps.currencies.services.resolver import resolve_rate

    route("USD")
    rate("USD", "55000", quote="IRT")  # a provider that speaks Toman
    resolved = resolve_rate("USD", "IRR", "purchase")
    assert resolved.rate == D("550000") and resolved.quoted_in == "IRT" and resolved.raw_rate == D("55000")


def test_foreign_display_currency_is_indicative_and_uses_display_route():
    fx()
    rs = make_ruleset(target_profit_percent="0")
    calc = calculate(PriceInput(original_amount=D("1000"), original_currency="TRY", presentation_currency="USD"), rs)
    assert calc.unit_display == (D("15000000") / D("550000")).quantize(D("0.01"))
    assert calc.display_rate.purpose == "display"


# ---------------------------------------------------------------------------------------- precision / rounding
def test_decimal_precision_no_float_drift():
    route("TRY")
    rate("TRY", "14999.5555555555")
    rs = make_ruleset(target_profit_percent="0")
    calc = calculate(PriceInput(original_amount=D("19.99"), original_currency="TRY", presentation_currency="IRR"), rs)
    exact = D("19.99") * D("14999.5555555555")
    assert exact == D("299841.115555554445")
    assert calc.item_irr == D("299841")  # rounded HALF_UP to a whole rial, once
    assert isinstance(calc.item_irr, D)


def test_no_float_anywhere_in_financial_code():
    root = Path(__file__).resolve().parent.parent / "apps"
    for app in ("pricing", "currencies"):
        for path in (root / app).rglob("*.py"):
            if "migrations" in path.parts:
                continue
            text = path.read_text()
            assert "float(" not in text, path
            assert "FloatField" not in text, path


@pytest.mark.parametrize(
    ("mode", "price", "expected"),
    [
        ("nearest", "25687500", "25700000"),
        ("nearest", "25650000", "25700000"),
        ("nearest", "25649999", "25600000"),
        ("up", "25600001", "25700000"),
        ("up", "25600000", "25600000"),
        ("down", "25699999", "25600000"),
    ],
)
def test_rounding_modes_to_step(mode, price, expected):
    route("TRY")
    rate("TRY", "1")
    rs = make_ruleset(
        target_profit_percent="0",
        rules=[{"kind": "rounding", "code": "r", "rounding_mode": mode, "rounding_step_irr": "100000"}],
    )
    calc = calculate(PriceInput(original_amount=D(price), original_currency="TRY", presentation_currency="IRR"), rs)
    assert calc.unit_price_irr == D(expected)


def test_half_up_on_component_boundaries():
    route("TRY")
    rate("TRY", "1")
    rs = make_ruleset(
        target_profit_percent="0",
        rules=[{"kind": "service_fee", "code": "s", "calc_method": "percent_of_item", "amount": "0.5"}],
    )
    calc = calculate(PriceInput(original_amount=D("1001"), original_currency="TRY", presentation_currency="IRR"), rs)
    assert [c.amount_irr for c in calc.components if c.kind == "service_fee"] == [D("5")]  # 5.005 -> 5
    calc = calculate(PriceInput(original_amount=D("1100"), original_currency="TRY", presentation_currency="IRR"), rs)
    assert [c.amount_irr for c in calc.components if c.kind == "service_fee"] == [D("6")]  # 5.5 -> 6 (half up)


def test_components_add_up_exactly():
    fx()
    scenario = SCENARIOS["scenarios"][0]
    calc = run(scenario)
    in_price = sum(c.amount_irr for c in calc.components if c.in_price)
    assert in_price == calc.unit_price_irr


# ---------------------------------------------------------------------------------------- pending / unknown never zero
def test_pending_profit_method_is_a_cost_only_preview():
    fx()
    rs = make_ruleset(profit_method="pending", publish=False)
    calc = calculate(PriceInput(original_amount=D("1000"), original_currency="TRY", presentation_currency="IRR"), rs)
    assert calc.computed and not calc.publishable
    assert [b.code for b in calc.blockers] == ["profit_method_pending"]
    assert calc.unit_price_irr == D("15000000")  # cost only: no invented profit


def test_missing_required_shipping_and_customs_never_default_to_zero():
    fx()
    rs = make_ruleset(required_cost_kinds=["shipping", "customs"], required_for_fulfillment=["PROXY_PURCHASE"])
    calc = calculate(
        PriceInput(
            original_amount=D("1000"),
            original_currency="TRY",
            fulfillment_type="PROXY_PURCHASE",
            presentation_currency="IRR",
        ),
        rs,
    )
    assert not calc.publishable
    assert sorted(b.rule for b in calc.blockers if b.code == "missing_required_cost") == ["customs", "shipping"]
    other = calculate(
        PriceInput(
            original_amount=D("1000"),
            original_currency="TRY",
            fulfillment_type="DIRECT_INVENTORY",
            presentation_currency="IRR",
        ),
        rs,
    )
    assert other.publishable  # the requirement only covers the configured fulfilment types


def test_explicit_confirmed_zero_cost_satisfies_a_required_cost():
    fx()
    rules = [
        {
            "kind": "shipping",
            "code": "free-ship",
            "calc_method": "fixed",
            "amount": "0",
            "currency": "IRR",
            "source_note": "owner: free shipping",
        },
        {
            "kind": "customs",
            "code": "no-duty",
            "calc_method": "fixed",
            "amount": "0",
            "currency": "IRR",
            "source_note": "exempt per tariff sheet",
        },
    ]
    rs = make_ruleset(required_cost_kinds=["shipping", "customs"], rules=rules)
    calc = calculate(PriceInput(original_amount=D("1000"), original_currency="TRY", presentation_currency="IRR"), rs)
    assert calc.publishable


def test_unknown_cost_value_blocks_and_is_not_zero():
    fx()
    rs = make_ruleset(
        rules=[
            {"kind": "shipping", "code": "ship", "calc_method": "fixed", "value_status": "unknown", "source_note": ""}
        ]
    )
    calc = calculate(PriceInput(original_amount=D("1000"), original_currency="TRY", presentation_currency="IRR"), rs)
    assert [b.code for b in calc.blockers] == ["unknown_cost"] and not calc.publishable


def test_pending_charge_mode_blocks():
    fx()
    rs = make_ruleset(
        rules=[
            {
                "kind": "shipping",
                "code": "ship",
                "calc_method": "fixed",
                "amount": "1000",
                "currency": "IRR",
                "charge_mode": "pending",
            }
        ]
    )
    calc = calculate(PriceInput(original_amount=D("1000"), original_currency="TRY", presentation_currency="IRR"), rs)
    assert "charge_mode_pending" in [b.code for b in calc.blockers]


@pytest.mark.parametrize(
    ("policy", "code"), [("pending", "estimates_policy_pending"), ("block", "estimated_costs_not_allowed")]
)
def test_estimated_costs_follow_the_owner_policy(policy, code):
    fx()
    rules = [
        {
            "kind": "shipping",
            "code": "ship",
            "calc_method": "fixed",
            "amount": "1000",
            "currency": "IRR",
            "value_status": "estimated",
        }
    ]
    rs = make_ruleset(estimates_policy=policy, rules=rules)
    calc = calculate(PriceInput(original_amount=D("1000"), original_currency="TRY", presentation_currency="IRR"), rs)
    assert calc.has_estimated and code in [b.code for b in calc.blockers]
    allowed = calculate(
        PriceInput(original_amount=D("1000"), original_currency="TRY", presentation_currency="IRR"),
        make_ruleset(estimates_policy="allow", rules=rules),
    )
    assert allowed.publishable and allowed.has_estimated


def test_weight_missing_for_per_kg_blocks():
    fx()
    rs = make_ruleset(
        rules=[{"kind": "shipping", "code": "ship", "calc_method": "per_kg", "amount": "2000000", "currency": "IRR"}]
    )
    calc = calculate(PriceInput(original_amount=D("1000"), original_currency="TRY", presentation_currency="IRR"), rs)
    assert "weight_missing" in [b.code for b in calc.blockers]


def test_target_profit_is_data_not_code():
    fx()
    for target, expected in [("25", "18750000"), ("10", "16500000")]:
        rs = make_ruleset(target_profit_percent=target)
        calc = calculate(
            PriceInput(original_amount=D("1000"), original_currency="TRY", presentation_currency="IRR"), rs
        )
        assert calc.unit_price_irr == D(expected)
    no_target = calculate(
        PriceInput(original_amount=D("1000"), original_currency="TRY", presentation_currency="IRR"),
        make_ruleset(target_profit_percent=None),
    )
    assert "profit_target_missing" in [b.code for b in no_target.blockers]


# ---------------------------------------------------------------------------------------- scoped rules
def test_most_specific_profit_rule_wins_product_over_brand_over_category_over_default():
    route("TRY")
    rate("TRY", "1")
    shoes = f.category("shoes")
    nike = f.brand("Nike")
    rs = make_ruleset(
        rules=[
            {"kind": "profit_percent", "code": "cat", "amount": "30", "category": shoes},
            {"kind": "profit_percent", "code": "brand", "amount": "28", "brand": nike},
        ],
        publish=False,
    )

    def price(**kw):
        base = dict(
            original_amount=D("10000000"),
            original_currency="TRY",
            presentation_currency="IRR",
            category_path=f"/{shoes.pk}/",
        )
        return calculate(PriceInput(**{**base, **kw}), rs).unit_price_irr

    assert price(category_path="/999/") == D("12500000")  # default 25%
    assert price() == D("13000000")  # category 30%
    assert price(brand_id=nike.pk) == D("12800000")  # brand (28%) is more specific than category


def test_product_specific_rule_beats_everything():
    route("TRY")
    rate("TRY", "1")
    shoes = f.category("shoes")
    nike = f.brand("Nike")
    product = f.product("p-spec", brand_obj=nike, category_obj=shoes)
    rs = make_ruleset(
        rules=[
            {"kind": "profit_percent", "code": "brand", "amount": "28", "brand": nike},
            {"kind": "profit_percent", "code": "prod", "amount": "35", "product": product},
        ],
        publish=False,
    )
    inp = PriceInput(
        original_amount=D("10000000"),
        original_currency="TRY",
        presentation_currency="IRR",
        product_id=product.pk,
        brand_id=nike.pk,
    )
    assert calculate(inp, rs).unit_price_irr == D("13500000")


def test_tied_rules_are_ambiguous_and_block():
    route("TRY")
    rate("TRY", "1")
    nike = f.brand("Nike")
    rs = make_ruleset(
        rules=[
            {"kind": "profit_percent", "code": "a", "amount": "28", "brand": nike},
            {"kind": "profit_percent", "code": "b", "amount": "29", "brand": nike},
        ],
        publish=False,
    )
    calc = calculate(
        PriceInput(original_amount=D("1000"), original_currency="TRY", presentation_currency="IRR", brand_id=nike.pk),
        rs,
    )
    assert "ambiguous_rules" in [b.code for b in calc.blockers]


def test_country_specific_customs():
    fx()
    rs = make_ruleset(
        rules=[
            {
                "kind": "customs",
                "code": "duty-ir",
                "calc_method": "fixed",
                "amount": "1000000",
                "currency": "IRR",
                "destination_country": "IR",
                "slot": "duty",
            },
            {
                "kind": "customs",
                "code": "duty-ae",
                "calc_method": "fixed",
                "amount": "200000",
                "currency": "IRR",
                "destination_country": "AE",
                "slot": "duty",
            },
        ],
        target_profit_percent="0",
    )
    base = dict(original_amount=D("1000"), original_currency="TRY", presentation_currency="IRR")
    assert calculate(PriceInput(**base, destination_country="IR"), rs).unit_price_irr == D("16000000")
    assert calculate(PriceInput(**base, destination_country="AE"), rs).unit_price_irr == D("15200000")
    assert calculate(PriceInput(**base), rs).unit_price_irr == D("15000000")  # no destination -> neither rule matches


def test_rule_validity_window_and_disabled_rules_are_ignored():
    from datetime import timedelta

    from django.utils import timezone

    fx()
    now = timezone.now()
    rs = make_ruleset(
        rules=[
            {
                "kind": "service_fee",
                "code": "old",
                "calc_method": "fixed",
                "amount": "100",
                "currency": "IRR",
                "valid_until": now - timedelta(days=1),
                "valid_from": now - timedelta(days=9),
            },
            {
                "kind": "service_fee",
                "code": "off",
                "calc_method": "fixed",
                "amount": "100",
                "currency": "IRR",
                "is_enabled": False,
            },
        ],
        target_profit_percent="0",
    )
    calc = calculate(PriceInput(original_amount=D("1000"), original_currency="TRY", presentation_currency="IRR"), rs)
    assert calc.unit_price_irr == D("15000000")
    assert PricingRule.objects.filter(rule_set=rs).count() == 2


def test_margin_of_100_percent_is_impossible():
    fx()
    rs = make_ruleset(profit_method="margin", target_profit_percent="100", publish=False)
    calc = calculate(PriceInput(original_amount=D("1000"), original_currency="TRY", presentation_currency="IRR"), rs)
    assert "invalid_margin" in [b.code for b in calc.blockers]
