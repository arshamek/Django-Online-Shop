"""
The pricing engine. Pure arithmetic on Decimal (no float anywhere) driven by a PricingRuleSet.

Rounding policy (documented in PHASE6_OVERVIEW.md): every IRR component is rounded HALF_UP to a whole rial when it
is created, and later components are calculated from the *rounded* earlier ones. A human can therefore re-add the
printed breakdown and get the final price exactly (the invariant checked by quotes.verify_quote).

Nothing unknown is ever treated as zero: unknown / pending / missing values become `blockers`; the calculation is still
returned (as a cost-only preview) so staff can see what is missing, but `publishable` is False.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from django.utils import timezone

from apps.currencies.errors import RateError
from apps.currencies.models import Currency
from apps.currencies.services.resolver import ResolvedRate, resolve_rate

from ..models import (
    COST_KINDS,
    EXCLUSIVE_KINDS,
    CalcMethod,
    ChargeMode,
    EstimatesPolicy,
    PricingRule,
    PricingRuleSet,
    ProfitMethod,
    RoundingMode,
    RuleKind,
    ValueStatus,
)

ONE = Decimal(1)
HUNDRED = Decimal(100)
TEN = Decimal(10)


def q0(value: Decimal) -> Decimal:
    return value.quantize(ONE, rounding=ROUND_HALF_UP)


def q4(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


@dataclass
class PriceInput:
    original_amount: Decimal
    original_currency: str
    list_amount: Decimal | None = None  # source list price (when it differs from original_amount: a discount exists)
    variant_id: int | None = None
    product_id: int | None = None
    brand_id: int | None = None
    category_id: int | None = None
    category_path: str = ""
    source_id: int | None = None
    source_country: str = ""
    fulfillment_type: str = ""
    weight_grams: int | None = None
    destination_country: str = ""
    sales_model: str = ""
    shipping_method: str = ""
    presentation_currency: str = "IRT"
    at: datetime | None = None


@dataclass
class Blocker:
    code: str
    message: str = ""
    rule: str = ""

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "rule": self.rule}


@dataclass
class Component:
    kind: str  # item | purchase_fee | shipping | customs | service_fee | profit | rounding
    code: str = ""
    name: str = ""
    basis_irr: Decimal | None = None
    percent: Decimal | None = None
    amount_irr: Decimal = Decimal(0)
    in_price: bool = True
    charge_mode: str = ""
    value_status: str = ""
    rule_id: int | None = None
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "code": self.code,
            "name": self.name,
            "basis_irr": None if self.basis_irr is None else str(self.basis_irr),
            "percent": None if self.percent is None else str(self.percent),
            "amount_irr": str(self.amount_irr),
            "in_price": self.in_price,
            "charge_mode": self.charge_mode,
            "value_status": self.value_status,
            "rule_id": self.rule_id,
            "note": self.note,
        }


@dataclass
class PriceCalculation:
    computed: bool = False
    rule_version: int | None = None
    original_amount: Decimal | None = None
    original_currency: str = ""
    rate: ResolvedRate | None = None
    item_irr: Decimal = Decimal(0)
    components: list[Component] = field(default_factory=list)
    profit_method: str = ""
    profit_percent: Decimal | None = None
    profit_percent_irr: Decimal = Decimal(0)
    profit_fixed_irr: Decimal = Decimal(0)
    min_profit_irr: Decimal | None = None
    min_profit_applied: bool = False
    profit_irr: Decimal = Decimal(0)
    price_before_rounding_irr: Decimal = Decimal(0)
    rounding_adjustment_irr: Decimal = Decimal(0)
    unit_price_irr: Decimal = Decimal(0)
    unit_separate_irr: Decimal = Decimal(0)
    unit_total_irr: Decimal = Decimal(0)
    internal_cost_irr: Decimal = Decimal(0)
    realized_profit_irr: Decimal = Decimal(0)
    realized_markup_percent: Decimal | None = None
    realized_margin_percent: Decimal | None = None
    has_estimated: bool = False
    presentation_currency: str = "IRR"
    unit_display: Decimal = Decimal(0)
    display_rate: ResolvedRate | None = None
    blockers: list[Blocker] = field(default_factory=list)
    display_blockers: list[Blocker] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def publishable(self) -> bool:
        return self.computed and not self.blockers

    @property
    def quotable(self) -> bool:
        return self.publishable and not self.display_blockers

    def expires_hint(self):
        """Earliest moment any rate used stops being valid (None = no time limit)."""
        times = [r.usable_until for r in (self.rate, self.display_rate) if r is not None and r.usable_until is not None]
        return min(times) if times else None

    def as_dict(self) -> dict:
        return {
            "computed": self.computed,
            "publishable": self.publishable,
            "quotable": self.quotable,
            "rule_version": self.rule_version,
            "original": {
                "amount": None if self.original_amount is None else str(self.original_amount),
                "currency": self.original_currency,
            },
            "rate": self.rate.as_dict() if self.rate else None,
            "item_irr": str(self.item_irr),
            "components": [c.as_dict() for c in self.components],
            "profit": {
                "method": self.profit_method,
                "percent": None if self.profit_percent is None else str(self.profit_percent),
                "percent_part_irr": str(self.profit_percent_irr),
                "fixed_part_irr": str(self.profit_fixed_irr),
                "min_profit_irr": None if self.min_profit_irr is None else str(self.min_profit_irr),
                "min_profit_applied": self.min_profit_applied,
                "profit_irr": str(self.profit_irr),
            },
            "price_before_rounding_irr": str(self.price_before_rounding_irr),
            "rounding_adjustment_irr": str(self.rounding_adjustment_irr),
            "unit_price_irr": str(self.unit_price_irr),
            "unit_separate_irr": str(self.unit_separate_irr),
            "unit_total_irr": str(self.unit_total_irr),
            "internal_cost_irr": str(self.internal_cost_irr),
            "realized_profit_irr": str(self.realized_profit_irr),
            "realized_markup_percent": None
            if self.realized_markup_percent is None
            else str(self.realized_markup_percent),
            "realized_margin_percent": None
            if self.realized_margin_percent is None
            else str(self.realized_margin_percent),
            "has_estimated_costs": self.has_estimated,
            "presentation": {
                "currency": self.presentation_currency,
                "unit_amount": str(self.unit_display),
                "rate": self.display_rate.as_dict() if self.display_rate else None,
            },
            "blockers": [b.as_dict() for b in self.blockers],
            "display_blockers": [b.as_dict() for b in self.display_blockers],
            "warnings": list(self.warnings),
        }


# ------------------------------------------------------------------------------------------------ rule matching
def _specificity(rule: PricingRule, category_ids: list[int]) -> int:
    score = 0
    score += 1000 if rule.product_id else 0
    score += 500 if rule.brand_id else 0
    if rule.category_id:
        score += 100 + 10 * (category_ids.index(rule.category_id) if rule.category_id in category_ids else 0)
    score += 50 if rule.source_id else 0
    score += 20 if rule.source_country else 0
    score += 20 if rule.destination_country else 0
    score += 10 * sum(bool(x) for x in (rule.fulfillment_type, rule.sales_model, rule.shipping_method))
    return score


def rule_matches(rule: PricingRule, inp: PriceInput, category_ids: list[int], at: datetime) -> bool:
    if not rule.is_enabled:
        return False
    if rule.valid_from and at < rule.valid_from:
        return False
    if rule.valid_until and at >= rule.valid_until:
        return False
    checks = (
        (rule.source_id, inp.source_id),
        (rule.product_id, inp.product_id),
        (rule.brand_id, inp.brand_id),
        (rule.fulfillment_type, inp.fulfillment_type),
        (rule.sales_model, inp.sales_model),
        (rule.shipping_method, inp.shipping_method),
        (rule.source_country.upper(), (inp.source_country or "").upper()),
        (rule.destination_country.upper(), (inp.destination_country or "").upper()),
    )
    for wanted, actual in checks:
        if wanted and wanted != actual:
            return False
    return not (rule.category_id and rule.category_id not in category_ids)


def applicable_rules(rules, inp: PriceInput, at: datetime):
    """Returns (rules to apply, blockers). Exclusive kinds / slots keep only the most specific rule."""
    category_ids = [int(p) for p in (inp.category_path or "").strip("/").split("/") if p]
    matched = [r for r in rules if rule_matches(r, inp, category_ids, at)]
    blockers: list[Blocker] = []
    groups: dict[tuple, list[PricingRule]] = {}
    chosen: list[PricingRule] = []
    for rule in matched:
        if rule.kind in EXCLUSIVE_KINDS or rule.slot:
            groups.setdefault((rule.kind, rule.slot), []).append(rule)
        else:
            chosen.append(rule)
    for (kind, slot), members in groups.items():
        ranked = sorted(members, key=lambda r: (_specificity(r, category_ids), r.priority), reverse=True)
        top = ranked[0]
        if len(ranked) > 1 and (_specificity(ranked[1], category_ids), ranked[1].priority) == (
            _specificity(top, category_ids),
            top.priority,
        ):
            blockers.append(
                Blocker(
                    "ambiguous_rules", f"Rules {top.code} and {ranked[1].code} tie for {kind}/{slot or '-'}.", top.code
                )
            )
            continue
        chosen.append(top)
    chosen.sort(key=lambda r: (-r.priority, r.code))
    return chosen, blockers


# ------------------------------------------------------------------------------------------------ the calculation
def _to_irr(
    amount: Decimal, currency_code: str | None, at, calc: PriceCalculation, resolver, label: str
) -> Decimal | None:
    if currency_code in (None, "", "IRR"):
        return amount
    try:
        rate = resolver(currency_code, "IRR", "purchase", at)
    except RateError as exc:
        calc.blockers.append(Blocker(exc.code, exc.message, label))
        return None
    return amount * rate.rate


def calculate(inp: PriceInput, rule_set: PricingRuleSet, *, resolver=resolve_rate, rules=None) -> PriceCalculation:
    at = inp.at or timezone.now()
    calc = PriceCalculation(
        rule_version=rule_set.version, original_amount=inp.original_amount, original_currency=inp.original_currency
    )
    rules = list(rule_set.rules.select_related("currency").all()) if rules is None else rules

    # 1. exchange rate and item price -------------------------------------------------------------
    if inp.original_amount is None or Decimal(inp.original_amount) < 0:
        calc.blockers.append(Blocker("invalid_source_price", "The source price is missing or negative."))
        return calc
    try:
        calc.rate = resolver(inp.original_currency, "IRR", "purchase", at)
    except RateError as exc:
        calc.blockers.append(Blocker(exc.code, exc.message))
        return calc
    if calc.rate.is_stale:
        calc.blockers.append(
            Blocker("rate_stale", f"Using a stale {inp.original_currency} rate is not allowed for publication.")
        )
    calc.item_irr = q0(Decimal(inp.original_amount) * calc.rate.rate)
    calc.components.append(
        Component(
            "item", "item", "Item price (converted)", amount_irr=calc.item_irr, value_status=ValueStatus.CONFIRMED
        )
    )

    chosen, blockers = applicable_rules(rules, inp, at)
    calc.blockers += blockers
    by_kind: dict[str, list[PricingRule]] = {}
    for rule in chosen:
        by_kind.setdefault(rule.kind, []).append(rule)

    # 2. required costs must be *addressed* (never zero by omission) -------------------------------
    required = set(rule_set.required_cost_kinds or [])
    scopes = set(rule_set.required_for_fulfillment or [])
    if required and (not scopes or inp.fulfillment_type in scopes):
        for kind in COST_KINDS:
            if kind in required and not by_kind.get(kind):
                calc.blockers.append(
                    Blocker(
                        "missing_required_cost", f"No {kind} rule applies; a cost is never assumed to be zero.", kind
                    )
                )

    # 3. cost components in stage order -------------------------------------------------------------
    running = calc.item_irr
    for kind in COST_KINDS:
        for rule in by_kind.get(kind, []):
            comp = Component(
                kind,
                rule.code,
                rule.name,
                charge_mode=rule.charge_mode,
                value_status=rule.value_status,
                rule_id=rule.pk,
            )
            if rule.value_status == ValueStatus.UNKNOWN or rule.amount is None:
                calc.blockers.append(
                    Blocker("unknown_cost", f"The value of '{rule.code}' is not known yet.", rule.code)
                )
                comp.note = "unknown"
                calc.components.append(comp)
                continue
            if rule.charge_mode == ChargeMode.PENDING:
                calc.blockers.append(
                    Blocker("charge_mode_pending", f"How '{rule.code}' is charged is not decided yet.", rule.code)
                )
            amount = _cost_amount(rule, inp, calc, running, at, resolver)
            if amount is None:
                comp.note = "not_computable"
                calc.components.append(comp)
                continue
            if rule.min_irr is not None:
                amount = max(amount, rule.min_irr)
            if rule.max_irr is not None:
                amount = min(amount, rule.max_irr)
            comp.amount_irr = q0(amount)
            comp.percent = (
                rule.amount if rule.calc_method in (CalcMethod.PERCENT_OF_ITEM, CalcMethod.PERCENT_OF_BASE) else None
            )
            comp.basis_irr = {CalcMethod.PERCENT_OF_ITEM: calc.item_irr, CalcMethod.PERCENT_OF_BASE: running}.get(
                rule.calc_method
            )
            comp.in_price = rule.charge_mode in (ChargeMode.INCLUDED, ChargeMode.PENDING)
            if rule.value_status == ValueStatus.ESTIMATED:
                calc.has_estimated = True
            calc.components.append(comp)
            running += comp.amount_irr

    # 4. estimates policy --------------------------------------------------------------------------
    if calc.has_estimated and rule_set.estimates_policy != EstimatesPolicy.ALLOW:
        code = (
            "estimates_policy_pending"
            if rule_set.estimates_policy == EstimatesPolicy.PENDING
            else "estimated_costs_not_allowed"
        )
        calc.blockers.append(
            Blocker(code, "Some costs are only estimates and the owner has not allowed publishing them.")
        )

    # 5. profit ------------------------------------------------------------------------------------
    cost_components = [c for c in calc.components if c.kind in COST_KINDS]
    included = sum((c.amount_irr for c in cost_components if c.in_price), Decimal(0))
    separate = sum((c.amount_irr for c in cost_components if c.charge_mode == ChargeMode.SEPARATE), Decimal(0))
    absorbed = sum((c.amount_irr for c in cost_components if c.charge_mode == ChargeMode.ABSORBED), Decimal(0))
    profit_base = calc.item_irr + included
    calc.profit_method = rule_set.profit_method
    _profit(calc, rule_set, by_kind, profit_base, absorbed, inp, at, resolver)

    # 6. rounding ----------------------------------------------------------------------------------
    calc.price_before_rounding_irr = profit_base + calc.profit_irr
    calc.unit_price_irr = calc.price_before_rounding_irr
    rounding = (by_kind.get(RuleKind.ROUNDING) or [None])[0]
    if rounding is not None:
        calc.unit_price_irr = _round_to_step(calc.price_before_rounding_irr, rounding)
        calc.rounding_adjustment_irr = calc.unit_price_irr - calc.price_before_rounding_irr
        calc.components.append(
            Component(
                "rounding", rounding.code, rounding.name, amount_irr=calc.rounding_adjustment_irr, rule_id=rounding.pk
            )
        )
    else:
        calc.warnings.append("no_rounding_rule")
    if calc.unit_price_irr % TEN:
        calc.warnings.append("price_not_whole_toman")
    if calc.unit_price_irr <= 0:
        calc.blockers.append(Blocker("non_positive_price", "The calculated price is not positive."))

    calc.unit_separate_irr = separate
    calc.unit_total_irr = calc.unit_price_irr + separate
    calc.internal_cost_irr = calc.item_irr + included + absorbed
    calc.realized_profit_irr = calc.unit_price_irr - calc.internal_cost_irr
    if calc.internal_cost_irr > 0:
        calc.realized_markup_percent = q4(calc.realized_profit_irr / calc.internal_cost_irr * HUNDRED)
    if calc.unit_price_irr > 0:
        calc.realized_margin_percent = q4(calc.realized_profit_irr / calc.unit_price_irr * HUNDRED)
    calc.computed = True

    # 7. presentation ------------------------------------------------------------------------------
    _present(calc, inp, at, resolver)
    return calc


def _cost_amount(rule, inp, calc, running, at, resolver):
    if rule.calc_method == CalcMethod.PERCENT_OF_ITEM:
        return calc.item_irr * rule.amount / HUNDRED
    if rule.calc_method == CalcMethod.PERCENT_OF_BASE:
        return running * rule.amount / HUNDRED
    unit_irr = _to_irr(rule.amount, rule.currency_id, at, calc, resolver, rule.code)
    if unit_irr is None:
        return None
    if rule.calc_method == CalcMethod.PER_KG:
        if not inp.weight_grams:
            calc.blockers.append(
                Blocker("weight_missing", f"'{rule.code}' is charged per kg but the variant has no weight.", rule.code)
            )
            return None
        return unit_irr * Decimal(inp.weight_grams) / Decimal(1000)
    return unit_irr


def _profit(calc, rule_set, by_kind, profit_base, absorbed, inp, at, resolver):
    method = rule_set.profit_method
    if method == ProfitMethod.PENDING:
        calc.blockers.append(
            Blocker("profit_method_pending", "The owner has not chosen markup or margin; showing cost only.")
        )
        return
    rule = (by_kind.get(RuleKind.PROFIT_PERCENT) or [None])[0]
    percent = rule.amount if rule is not None and rule.amount is not None else rule_set.target_profit_percent
    if rule is not None and rule.amount is None:
        calc.blockers.append(
            Blocker("unknown_profit_percent", f"Profit rule '{rule.code}' has no percentage.", rule.code)
        )
    if percent is None:
        calc.blockers.append(Blocker("profit_target_missing", "No target profit percentage is configured."))
        return
    calc.profit_percent = Decimal(percent)
    if method == ProfitMethod.MARKUP:
        calc.profit_percent_irr = q0(profit_base * calc.profit_percent / HUNDRED)
    else:
        if calc.profit_percent >= HUNDRED:
            calc.blockers.append(Blocker("invalid_margin", "A margin of 100% or more is impossible."))
            return
        calc.profit_percent_irr = q0(profit_base * calc.profit_percent / (HUNDRED - calc.profit_percent))
    fixed = Decimal(0)
    for fixed_rule in by_kind.get(RuleKind.PROFIT_FIXED, []):
        if fixed_rule.amount is None or fixed_rule.value_status == ValueStatus.UNKNOWN:
            calc.blockers.append(
                Blocker("unknown_cost", f"Fixed profit '{fixed_rule.code}' has no value.", fixed_rule.code)
            )
            continue
        value = _to_irr(fixed_rule.amount, fixed_rule.currency_id, at, calc, resolver, fixed_rule.code)
        if value is not None:
            fixed += q0(value)
    calc.profit_fixed_irr = fixed
    profit = calc.profit_percent_irr + fixed
    floor_rule = (by_kind.get(RuleKind.MIN_PROFIT) or [None])[0]
    if floor_rule is not None and floor_rule.amount is not None:
        floor = _to_irr(floor_rule.amount, floor_rule.currency_id, at, calc, resolver, floor_rule.code)
        if floor is not None:
            calc.min_profit_irr = q0(floor)
            realized = profit - absorbed
            if realized < calc.min_profit_irr:
                profit += calc.min_profit_irr - realized
                calc.min_profit_applied = True
    calc.profit_irr = profit
    calc.components.append(
        Component(
            "profit",
            "profit",
            f"Profit ({method})",
            basis_irr=profit_base,
            percent=calc.profit_percent,
            amount_irr=profit,
            rule_id=rule.pk if rule is not None else None,
        )
    )


def _round_to_step(value: Decimal, rule: PricingRule) -> Decimal:
    step = rule.rounding_step_irr
    mode = {RoundingMode.NEAREST: ROUND_HALF_UP, RoundingMode.UP: ROUND_CEILING, RoundingMode.DOWN: ROUND_FLOOR}[
        rule.rounding_mode
    ]
    return (value / step).quantize(ONE, rounding=mode) * step


def _present(calc: PriceCalculation, inp: PriceInput, at, resolver):
    code = inp.presentation_currency or "IRR"
    calc.presentation_currency = code
    if code == "IRR":
        calc.unit_display = calc.unit_price_irr
        return
    currency = Currency.objects.filter(code=code, is_active=True, is_display=True).first()
    if currency is None:
        calc.display_blockers.append(
            Blocker("display_currency_unsupported", f"{code} is not an enabled display currency.")
        )
        return
    if currency.base_currency_id == "IRR":  # Toman: an exact unit conversion
        calc.unit_display = calc.unit_price_irr / currency.base_factor
        return
    try:
        calc.display_rate = resolver(code, "IRR", "display", at)
    except RateError as exc:
        calc.display_blockers.append(Blocker(f"display_{exc.code}", exc.message))
        return
    if calc.display_rate.is_stale:
        calc.display_blockers.append(Blocker("display_rate_stale", "The display rate is stale."))
    quantum = Decimal(1).scaleb(-currency.decimal_places)
    calc.unit_display = (calc.unit_price_irr / calc.display_rate.rate).quantize(quantum, rounding=ROUND_HALF_UP)
