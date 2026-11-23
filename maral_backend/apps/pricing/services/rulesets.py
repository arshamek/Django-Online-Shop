"""Rule-set lifecycle: draft -> preview -> publish. Published sets are immutable; edits happen in a draft."""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.audit import services as audit
from apps.common.exceptions import BusinessRuleViolation, Conflict
from apps.currencies.models import Currency, RateRoute

from ..errors import PriceUnavailable
from ..models import (
    COST_KINDS,
    ChargeMode,
    EstimatesPolicy,
    PricingRule,
    PricingRuleSet,
    ProfitMethod,
    RuleKind,
    RuleSetStatus,
    ValueStatus,
)

DECISION_FIELDS = (
    "profit_method",
    "target_profit_percent",
    "target_profit_confirmed",
    "required_cost_kinds",
    "required_for_fulfillment",
    "estimates_policy",
    "quote_validity_minutes",
    "source_price_basis",
    "customer_breakdown",
    "default_destination_country",
)
RULE_FIELDS = [
    "code",
    "name",
    "kind",
    "calc_method",
    "amount",
    "currency",
    "value_status",
    "charge_mode",
    "min_irr",
    "max_irr",
    "rounding_mode",
    "rounding_step_irr",
    "source",
    "source_country",
    "destination_country",
    "category",
    "brand",
    "product",
    "fulfillment_type",
    "sales_model",
    "shipping_method",
    "slot",
    "priority",
    "valid_from",
    "valid_until",
    "is_enabled",
    "source_note",
    "confirmed_by",
    "confirmed_at",
]


def active_ruleset(at=None) -> PricingRuleSet:
    at = at or timezone.now()
    rule_set = (
        PricingRuleSet.objects.filter(status=RuleSetStatus.PUBLISHED)
        .filter(Q(effective_from__isnull=True) | Q(effective_from__lte=at))
        .order_by("-version")
        .first()
    )
    if rule_set is None:
        raise PriceUnavailable(
            "No pricing rule set has been published.",
            code="no_published_ruleset",
            blockers=[{"code": "no_published_ruleset", "message": "No pricing rule set has been published."}],
        )
    return rule_set


def current_ruleset_or_none(at=None):
    try:
        return active_ruleset(at)
    except PriceUnavailable:
        return None


@transaction.atomic
def create_draft(*, actor=None, from_set: PricingRuleSet | None = None, name: str = "", request=None) -> PricingRuleSet:
    """A new editable draft, cloned from `from_set` (default: the active published set) or blank."""
    if from_set is None:
        from_set = current_ruleset_or_none()
    draft = PricingRuleSet(name=name, created_by=actor, cloned_from=from_set)
    if from_set is not None:
        for field in DECISION_FIELDS:
            setattr(draft, field, getattr(from_set, field))
    else:
        draft.required_cost_kinds = [RuleKind.SHIPPING, RuleKind.CUSTOMS]
        draft.required_for_fulfillment = ["PROXY_PURCHASE"]
    draft.save()
    if from_set is not None:
        PricingRule.objects.bulk_create(
            [PricingRule(rule_set=draft, **{f: getattr(r, f) for f in _attnames(r)}) for r in from_set.rules.all()]
        )
    audit.record("pricing.draft_created", request=request, actor=actor, target=draft, version=draft.version)
    return draft


def _attnames(rule):
    return [
        (f"{f}_id") if f in ("currency", "source", "category", "brand", "product", "confirmed_by") else f
        for f in RULE_FIELDS
    ]


@transaction.atomic
def publish(rule_set: PricingRuleSet, *, actor=None, request=None) -> PricingRuleSet:
    rule_set = PricingRuleSet.objects.select_for_update().get(pk=rule_set.pk)
    if rule_set.status != RuleSetStatus.DRAFT:
        raise Conflict("Only a draft can be published.", code="not_a_draft")
    problems = hard_problems(rule_set)
    if problems:
        raise BusinessRuleViolation("The draft is inconsistent.", code="invalid_ruleset", fields={"rules": problems})
    now = timezone.now()
    PricingRuleSet.objects.filter(status=RuleSetStatus.PUBLISHED).update(status=RuleSetStatus.ARCHIVED)
    rule_set.status, rule_set.published_at, rule_set.published_by = RuleSetStatus.PUBLISHED, now, actor
    rule_set.effective_from = rule_set.effective_from or now
    super(PricingRuleSet, rule_set).save()  # bypass the draft guard: this is the one allowed transition
    audit.record("pricing.ruleset_published", request=request, actor=actor, target=rule_set, version=rule_set.version)
    version = rule_set.version
    from .. import tasks

    transaction.on_commit(lambda: tasks.reprice_all.delay(cause="rule_change", version=version))
    return rule_set


def hard_problems(rule_set: PricingRuleSet) -> list[str]:
    """Things that make a rule set unusable. Pending decisions are NOT hard problems (see pending_decisions)."""
    problems = []
    codes = set()
    for rule in rule_set.rules.all():
        if rule.code in codes:
            problems.append(f"duplicate code {rule.code}")
        codes.add(rule.code)
        try:
            rule.clean()
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{rule.code}: {getattr(exc, 'message_dict', exc)}")
    if (
        rule_set.profit_method == ProfitMethod.MARGIN
        and rule_set.target_profit_percent is not None
        and rule_set.target_profit_percent >= 100
    ):
        problems.append("a margin target of 100% or more is impossible")
    return problems


def pending_decisions(rule_set: PricingRuleSet) -> list[dict]:
    """What still needs the owner / outside information, in plain words (mirrors docs/pricing/PHASE6_DECISIONS.md)."""
    rules = list(rule_set.rules.filter(is_enabled=True))
    out = []

    def add(ref, key, title, consequence):
        out.append({"ref": ref, "key": key, "title": title, "consequence": consequence})

    if rule_set.profit_method == ProfitMethod.PENDING:
        add(
            "B-2", "profit_method", "Choose markup or margin", "No price can be published; calculations show cost only."
        )
    if rule_set.target_profit_percent is None:
        add(
            "B-3",
            "target_profit",
            "Set the target profit percentage",
            "Without it (and without profit rules) no price can be published.",
        )
    elif not rule_set.target_profit_confirmed:
        add(
            "B-3",
            "target_profit_unconfirmed",
            "Confirm the initial target profit",
            "The current figure is an initial value; prices use it but it is not owner-approved.",
        )
    if rule_set.estimates_policy == EstimatesPolicy.PENDING:
        add(
            "B-16",
            "estimates_policy",
            "Decide whether estimated costs may be published",
            "Any estimated cost blocks publication.",
        )
    if any(r.kind in COST_KINDS and r.charge_mode == ChargeMode.PENDING for r in rules):
        add(
            "B-1",
            "charge_mode",
            "Decide how each cost is charged (included / separate / absorbed)",
            "Costs with a pending mode block publication.",
        )
    if any(r.value_status == ValueStatus.UNKNOWN and r.kind in COST_KINDS for r in rules):
        add(
            "E-1/E-2",
            "unknown_costs",
            "Provide the missing tariff / customs values",
            "Rules with unknown values block publication.",
        )
    for kind in rule_set.required_cost_kinds or []:
        if not any(r.kind == kind for r in rules):
            add(
                "B-7/B-8",
                f"required_{kind}",
                f"Define the {kind} rule (or an explicit zero-cost rule)",
                f"Required-cost items stay unpriced: {kind} is never assumed to be zero.",
            )
    if not any(r.kind == RuleKind.ROUNDING for r in rules):
        add("B-10", "rounding", "Choose a rounding step", "Prices will be whole rials with no rounding.")
    for code in Currency.objects.filter(is_source=True, is_active=True).values_list("code", flat=True):
        if not RateRoute.objects.filter(base=code, quote="IRR", purpose="purchase", is_enabled=True).exists():
            add(
                "B-5",
                f"rate_route_{code}",
                f"Configure the {code} -> IRR rate route",
                f"Products priced in {code} cannot be priced.",
            )
    return out
