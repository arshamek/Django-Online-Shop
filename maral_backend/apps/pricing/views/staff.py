"""Pricing administration API. Needs `accounts.pricing_manage`. Published rule sets are immutable; edit a draft, preview, publish."""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import HasStaffPermission, IsStaff
from apps.accounts.views.base import NoStoreMixin
from apps.audit import services as audit
from apps.catalog.models import ProductVariant
from apps.common.api.pagination import StandardPagination
from apps.common.exceptions import BusinessRuleViolation
from apps.currencies.views.public import plain

from .. import tasks
from ..models import (
    COST_KINDS,
    PriceHistory,
    PriceQuote,
    PricingRule,
    PricingRuleSet,
    RuleSetStatus,
)
from ..services import inputs, quotes, repricing, rulesets
from ..services.engine import PriceInput, calculate
from .public import quote_out

PERM = "accounts.pricing_manage"
TAG = ["staff-pricing"]
MONEY = dict(max_digits=24, decimal_places=6, min_value=Decimal("0"))


class StaffView(NoStoreMixin, APIView):
    permission_classes = [IsAuthenticated, IsStaff, HasStaffPermission]
    parser_classes = [JSONParser]
    required_permissions = {"GET": PERM, "POST": PERM, "PATCH": PERM, "DELETE": PERM}


def _set(version) -> PricingRuleSet:
    obj = PricingRuleSet.objects.filter(version=version).first()
    if obj is None:
        raise NotFound()
    return obj


def _draft(version) -> PricingRuleSet:
    obj = _set(version)
    if not obj.is_editable:
        raise BusinessRuleViolation(
            "Published rule sets are immutable. Create a draft from it instead.", code="ruleset_not_editable"
        )
    return obj


def set_out(rs: PricingRuleSet, *, detail=False) -> dict:
    out = {
        "version": rs.version,
        "name": rs.name,
        "status": rs.status,
        "notes": rs.notes,
        "created_at": rs.created_at,
        "published_at": rs.published_at,
        "effective_from": rs.effective_from,
        "decisions": {
            "profit_method": rs.profit_method,
            "target_profit_percent": None if rs.target_profit_percent is None else plain(rs.target_profit_percent),
            "target_profit_confirmed": rs.target_profit_confirmed,
            "required_cost_kinds": rs.required_cost_kinds,
            "required_for_fulfillment": rs.required_for_fulfillment,
            "estimates_policy": rs.estimates_policy,
            "quote_validity_minutes": rs.quote_validity_minutes,
            "source_price_basis": rs.source_price_basis,
            "customer_breakdown": rs.customer_breakdown,
            "default_destination_country": rs.default_destination_country,
        },
    }
    if detail:
        out["rules"] = [rule_out(r) for r in rs.rules.select_related("currency")]
        out["pending_decisions"] = rulesets.pending_decisions(rs)
    return out


def rule_out(r: PricingRule) -> dict:
    return {
        "code": r.code,
        "name": r.name,
        "kind": r.kind,
        "calc_method": r.calc_method,
        "amount": None if r.amount is None else plain(r.amount),
        "currency": r.currency_id,
        "value_status": r.value_status,
        "charge_mode": r.charge_mode,
        "min_irr": None if r.min_irr is None else plain(r.min_irr),
        "max_irr": None if r.max_irr is None else plain(r.max_irr),
        "rounding_mode": r.rounding_mode,
        "rounding_step_irr": None if r.rounding_step_irr is None else plain(r.rounding_step_irr),
        "scope": {
            "source": r.source_id,
            "source_country": r.source_country,
            "destination_country": r.destination_country,
            "category": r.category_id,
            "brand": r.brand_id,
            "product": r.product_id,
            "fulfillment_type": r.fulfillment_type,
            "sales_model": r.sales_model,
            "shipping_method": r.shipping_method,
        },
        "slot": r.slot,
        "priority": r.priority,
        "valid_from": r.valid_from,
        "valid_until": r.valid_until,
        "is_enabled": r.is_enabled,
        "source_note": r.source_note,
        "confirmed_at": r.confirmed_at,
    }


class DecisionsIn(serializers.Serializer):
    profit_method = serializers.ChoiceField(choices=["pending", "markup", "margin"], required=False)
    target_profit_percent = serializers.DecimalField(
        max_digits=7,
        decimal_places=4,
        min_value=Decimal("0"),
        max_value=Decimal("1000"),
        required=False,
        allow_null=True,
    )
    target_profit_confirmed = serializers.BooleanField(required=False)
    required_cost_kinds = serializers.ListField(child=serializers.ChoiceField(choices=list(COST_KINDS)), required=False)
    required_for_fulfillment = serializers.ListField(child=serializers.CharField(max_length=20), required=False)
    estimates_policy = serializers.ChoiceField(choices=["pending", "allow", "block"], required=False)
    quote_validity_minutes = serializers.IntegerField(min_value=1, max_value=1440, required=False)
    source_price_basis = serializers.ChoiceField(choices=["effective", "sale_or_list", "list"], required=False)
    customer_breakdown = serializers.ChoiceField(choices=["none", "summary", "itemized"], required=False)
    default_destination_country = serializers.CharField(max_length=2, required=False, allow_blank=True)


class RuleSetListView(StaffView):
    class CreateIn(serializers.Serializer):
        name = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
        from_version = serializers.IntegerField(required=False)
        blank = serializers.BooleanField(required=False, default=False)

    @extend_schema(
        tags=TAG, operation_id="staff_pricing_rulesets_list", summary="Rule-set versions", responses={200: dict}
    )
    def get(self, request):
        return Response({"results": [set_out(rs) for rs in PricingRuleSet.objects.all()]})

    @extend_schema(
        tags=TAG,
        summary="Start a draft (clone of the active set, of a version, or blank)",
        request=CreateIn,
        responses={201: dict},
    )
    def post(self, request):
        data = self.CreateIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        source = (
            None
            if v["blank"]
            else (_set(v["from_version"]) if v.get("from_version") else rulesets.current_ruleset_or_none())
        )
        if PricingRuleSet.objects.filter(status=RuleSetStatus.DRAFT).count() >= 5:
            raise BusinessRuleViolation("Too many open drafts; publish or delete one first.", code="too_many_drafts")
        draft = rulesets.create_draft(actor=request.user, from_set=source, name=v["name"], request=request)
        if v["blank"] or source is None:
            draft.refresh_from_db()
        return Response(set_out(draft, detail=True), status=201)


class RuleSetDetailView(StaffView):
    @extend_schema(tags=TAG, summary="A rule set with its rules and pending decisions", responses={200: dict})
    def get(self, request, ver):
        version = ver
        return Response(set_out(_set(version), detail=True))

    @extend_schema(
        tags=TAG,
        summary="Change the decisions of a DRAFT (profit method, target, estimates policy ...)",
        request=DecisionsIn,
        responses={200: dict},
    )
    def patch(self, request, ver):
        version = ver
        rs = _draft(version)
        data = DecisionsIn(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        for key, value in data.validated_data.items():
            setattr(rs, key, value)
        if rs.profit_method == "margin" and rs.target_profit_percent is not None and rs.target_profit_percent >= 100:
            raise BusinessRuleViolation("A margin target must be below 100%.", code="invalid_margin")
        if "target_profit_percent" in data.validated_data and "target_profit_confirmed" not in data.validated_data:
            rs.target_profit_confirmed = False  # a changed figure is unconfirmed until the owner says otherwise
        rs.save()
        audit.record(
            "pricing.decisions_changed",
            request=request,
            target=rs,
            version=rs.version,
            changed=sorted(data.validated_data),
        )
        return Response(set_out(rs, detail=True))

    @extend_schema(tags=TAG, summary="Delete a draft", responses={204: None})
    def delete(self, request, ver):
        version = ver
        rs = _draft(version)
        audit.record("pricing.draft_deleted", request=request, target=rs, version=rs.version)
        rs.delete()
        return Response(status=204)


class RuleSetPublishView(StaffView):
    @extend_schema(
        tags=TAG, summary="Publish a draft (it becomes immutable and active)", request=None, responses={200: dict}
    )
    def post(self, request, ver):
        version = ver
        rs = rulesets.publish(_set(version), actor=request.user, request=request)
        return Response(set_out(rs, detail=True))


class RuleIn(serializers.Serializer):
    code = serializers.SlugField(max_length=60)
    name = serializers.CharField(max_length=160)
    kind = serializers.ChoiceField(choices=[c for c, _ in PricingRule._meta.get_field("kind").choices])
    calc_method = serializers.ChoiceField(
        choices=[c for c, _ in PricingRule._meta.get_field("calc_method").choices], required=False
    )
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=6, min_value=Decimal("0"), required=False, allow_null=True
    )
    currency = serializers.CharField(max_length=3, required=False, allow_null=True, allow_blank=True)
    value_status = serializers.ChoiceField(choices=["confirmed", "estimated", "unknown"], required=False)
    charge_mode = serializers.ChoiceField(choices=["pending", "included", "separate", "absorbed"], required=False)
    min_irr = serializers.DecimalField(
        max_digits=18, decimal_places=0, min_value=Decimal("0"), required=False, allow_null=True
    )
    max_irr = serializers.DecimalField(
        max_digits=18, decimal_places=0, min_value=Decimal("0"), required=False, allow_null=True
    )
    rounding_mode = serializers.ChoiceField(choices=["nearest", "up", "down"], required=False, allow_blank=True)
    rounding_step_irr = serializers.DecimalField(
        max_digits=18, decimal_places=0, min_value=Decimal("10"), required=False, allow_null=True
    )
    source = serializers.IntegerField(required=False, allow_null=True)
    source_country = serializers.CharField(max_length=2, required=False, allow_blank=True)
    destination_country = serializers.CharField(max_length=2, required=False, allow_blank=True)
    category = serializers.IntegerField(required=False, allow_null=True)
    brand = serializers.IntegerField(required=False, allow_null=True)
    product = serializers.IntegerField(required=False, allow_null=True)
    fulfillment_type = serializers.CharField(max_length=20, required=False, allow_blank=True)
    sales_model = serializers.CharField(max_length=40, required=False, allow_blank=True)
    shipping_method = serializers.CharField(max_length=40, required=False, allow_blank=True)
    slot = serializers.CharField(max_length=40, required=False, allow_blank=True)
    priority = serializers.IntegerField(required=False)
    valid_from = serializers.DateTimeField(required=False, allow_null=True)
    valid_until = serializers.DateTimeField(required=False, allow_null=True)
    is_enabled = serializers.BooleanField(required=False)
    source_note = serializers.CharField(max_length=300, required=False, allow_blank=True)


FK_FIELDS = {
    "currency": "currency_id",
    "source": "source_id",
    "category": "category_id",
    "brand": "brand_id",
    "product": "product_id",
}


def _apply(rule: PricingRule, values: dict, user):
    for key, value in values.items():
        setattr(rule, FK_FIELDS.get(key, key), value or None if key in FK_FIELDS else value)
    if values.get("value_status") == "confirmed":
        rule.confirmed_by, rule.confirmed_at = user, timezone.now()
    try:
        rule.clean()
        rule.save()
    except Exception as exc:  # django ValidationError / IntegrityError -> 422 with the field messages
        fields = getattr(exc, "message_dict", None)
        raise BusinessRuleViolation(
            "The rule is not valid.", code="invalid_rule", fields=fields or {"rule": [str(exc)]}
        ) from None
    return rule


class RuleListView(StaffView):
    @extend_schema(
        tags=TAG, operation_id="staff_pricing_rules_list", summary="Rules of a rule set", responses={200: dict}
    )
    def get(self, request, ver):
        version = ver
        return Response({"results": [rule_out(r) for r in _set(version).rules.select_related("currency")]})

    @extend_schema(tags=TAG, summary="Add a rule to a DRAFT", request=RuleIn, responses={201: dict})
    def post(self, request, ver):
        version = ver
        rs = _draft(version)
        data = RuleIn(data=request.data)
        data.is_valid(raise_exception=True)
        if rs.rules.filter(code=data.validated_data["code"]).exists():
            raise BusinessRuleViolation("A rule with this code exists.", code="duplicate_code")
        rule = _apply(PricingRule(rule_set=rs), dict(data.validated_data), request.user)
        audit.record(
            "pricing.rule_saved", request=request, target=rule, version=rs.version, code=rule.code, kind=rule.kind
        )
        return Response(rule_out(rule), status=201)


class RuleDetailView(StaffView):
    def _rule(self, version, code):
        rule = _set(version).rules.filter(code=code).first()
        if rule is None:
            raise NotFound()
        return rule

    @extend_schema(tags=TAG, summary="One rule", responses={200: dict})
    def get(self, request, ver, code):
        version = ver
        return Response(rule_out(self._rule(version, code)))

    @extend_schema(tags=TAG, summary="Change a rule of a DRAFT", request=RuleIn, responses={200: dict})
    def patch(self, request, ver, code):
        version = ver
        _draft(version)
        rule = self._rule(version, code)
        data = RuleIn(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        values = dict(data.validated_data)
        values.pop("code", None)
        _apply(rule, values, request.user)
        audit.record(
            "pricing.rule_saved", request=request, target=rule, version=version, code=rule.code, changed=sorted(values)
        )
        return Response(rule_out(rule))

    @extend_schema(tags=TAG, summary="Remove a rule from a DRAFT", responses={204: None})
    def delete(self, request, ver, code):
        version = ver
        _draft(version)
        rule = self._rule(version, code)
        audit.record("pricing.rule_deleted", request=request, target=rule, version=version, code=code)
        rule.delete()
        return Response(status=204)


class ReadinessView(StaffView):
    @extend_schema(
        tags=TAG,
        summary="What still blocks pricing (owner decisions, missing values, missing rates)",
        responses={200: dict},
    )
    def get(self, request):
        version = request.query_params.get("version")
        rs = _set(int(version)) if version and version.isdigit() else rulesets.current_ruleset_or_none()
        if rs is None:
            return Response(
                {
                    "active_version": None,
                    "ready": False,
                    "pending_decisions": [
                        {
                            "ref": "T-4",
                            "key": "no_ruleset",
                            "title": "Create and publish the first pricing rule set",
                            "consequence": "Nothing can be priced.",
                        }
                    ],
                }
            )
        pending = rulesets.pending_decisions(rs)
        blocking = [p for p in pending if p["key"] not in ("rounding", "target_profit_unconfirmed")]
        return Response(
            {"version": rs.version, "status": rs.status, "ready": not blocking, "pending_decisions": pending}
        )


class PreviewIn(serializers.Serializer):
    variant_id = serializers.UUIDField(required=False)
    original_amount = serializers.DecimalField(max_digits=18, decimal_places=4, min_value=Decimal("0"), required=False)
    original_currency = serializers.CharField(max_length=3, required=False)
    weight_grams = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    brand = serializers.IntegerField(required=False, allow_null=True)
    category = serializers.IntegerField(required=False, allow_null=True)
    product = serializers.IntegerField(required=False, allow_null=True)
    source = serializers.IntegerField(required=False, allow_null=True)
    source_country = serializers.CharField(max_length=2, required=False, allow_blank=True, default="")
    fulfillment_type = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    destination_country = serializers.CharField(max_length=2, required=False, allow_blank=True, default="")
    sales_model = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    shipping_method = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    currency = serializers.CharField(max_length=3, required=False, default="IRT")
    rule_version = serializers.IntegerField(required=False)


class PreviewView(StaffView):
    @extend_schema(
        tags=TAG,
        summary="Preview a final price with its full breakdown (any rule set, including a draft)",
        request=PreviewIn,
        responses={200: dict},
    )
    def post(self, request):
        data = PreviewIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        rs = _set(v["rule_version"]) if v.get("rule_version") else rulesets.active_ruleset()
        if v.get("variant_id"):
            variant = (
                ProductVariant.objects.select_related("product__category", "product__brand", "source")
                .filter(public_id=v["variant_id"])
                .first()
            )
            if variant is None:
                raise NotFound()
            inp, reason = inputs.build_input(
                variant,
                rs,
                destination_country=v["destination_country"],
                sales_model=v["sales_model"],
                shipping_method=v["shipping_method"],
                presentation_currency=v["currency"],
            )
            if inp is None:
                raise BusinessRuleViolation("The variant has no source price.", code=reason)
        else:
            if v.get("original_amount") is None or not v.get("original_currency"):
                raise BusinessRuleViolation(
                    "Give a variant_id, or original_amount and original_currency.", code="input_required"
                )
            from apps.categories.models import Category

            path = (
                Category.objects.filter(pk=v["category"]).values_list("path", flat=True).first()
                if v.get("category")
                else ""
            )
            inp = PriceInput(
                original_amount=v["original_amount"],
                original_currency=v["original_currency"],
                weight_grams=v.get("weight_grams"),
                brand_id=v.get("brand"),
                category_id=v.get("category"),
                category_path=path or "",
                product_id=v.get("product"),
                source_id=v.get("source"),
                source_country=v["source_country"],
                fulfillment_type=v["fulfillment_type"],
                destination_country=v["destination_country"] or rs.default_destination_country,
                sales_model=v["sales_model"],
                shipping_method=v["shipping_method"],
                presentation_currency=v["currency"],
            )
        calc = calculate(inp, rs)
        return Response({"rule_version": rs.version, "rule_set_status": rs.status, **calc.as_dict()})


class RepriceIn(serializers.Serializer):
    variant_ids = serializers.ListField(child=serializers.UUIDField(), required=False, max_length=500)
    all = serializers.BooleanField(required=False, default=False)
    force = serializers.BooleanField(required=False, default=False)


class RepriceView(StaffView):
    @extend_schema(
        tags=TAG,
        summary="Recalculate stored prices (selected variants now, or all in the background)",
        request=RepriceIn,
        responses={200: dict},
    )
    def post(self, request):
        data = RepriceIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        if v["all"]:
            if v["force"]:
                raise BusinessRuleViolation("force cannot be combined with all.", code="invalid_request")
            transaction.on_commit(lambda: tasks.reprice_all.delay(cause="manual"))
            audit.record("pricing.reprice_all", request=request)
            return Response({"queued": True})
        ids = list(ProductVariant.objects.filter(public_id__in=v.get("variant_ids", [])).values_list("pk", flat=True))
        if not ids:
            raise BusinessRuleViolation("No variants selected.", code="input_required")
        counts = repricing.reprice_variants(ids, cause="manual", actor=request.user, force=v["force"])
        audit.record("pricing.reprice", request=request, count=len(ids), force=v["force"], **counts)
        return Response(counts)


def history_out(h: PriceHistory) -> dict:
    return {
        "id": h.pk,
        "sku": h.variant.sku,
        "variant_id": str(h.variant.public_id),
        "sequence": h.sequence,
        "outcome": h.outcome,
        "cause": h.cause,
        "old_final_irr": None if h.old_final_irr is None else plain(h.old_final_irr),
        "new_final_irr": None if h.new_final_irr is None else plain(h.new_final_irr),
        "old_list_irr": None if h.old_list_irr is None else plain(h.old_list_irr),
        "new_list_irr": None if h.new_list_irr is None else plain(h.new_list_irr),
        "rule_version": h.rule_version,
        "rate_id": h.rate_id,
        "rate_at": h.rate_at,
        "blockers": h.blockers,
        "calculation": h.calculation,
        "actor": h.actor.email if h.actor_id and h.actor.email else None,
        "created_at": h.created_at,
    }


class HistoryView(StaffView):
    @extend_schema(tags=TAG, summary="Price history (filter: variant, sku, outcome, cause)", responses={200: dict})
    def get(self, request):
        qs = PriceHistory.objects.select_related("variant", "actor")
        p = request.query_params
        if p.get("variant"):
            qs = qs.filter(variant__public_id=p["variant"])
        if p.get("sku"):
            qs = qs.filter(variant__sku=p["sku"])
        for field in ("outcome", "cause"):
            if p.get(field):
                qs = qs.filter(**{field: p[field]})
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response([history_out(h) for h in page])


class QuoteListView(StaffView):
    @extend_schema(
        tags=TAG, operation_id="staff_pricing_quotes_list", summary="Quotes (filter: status)", responses={200: dict}
    )
    def get(self, request):
        qs = PriceQuote.objects.select_related("display_currency")
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response([quote_out(q) for q in page])


class QuoteDetailView(StaffView):
    @extend_schema(tags=TAG, summary="A quote with its full calculation", responses={200: dict})
    def get(self, request, id):
        quote = PriceQuote.objects.prefetch_related("items__variant", "items__components").filter(public_id=id).first()
        if quote is None:
            raise NotFound()
        return Response(quote_out(quote, full=True))


class QuoteExpireView(StaffView):
    @extend_schema(tags=TAG, summary="Expire an open quote now", request=None, responses={200: dict})
    def post(self, request, id):
        quote = PriceQuote.objects.filter(public_id=id).first()
        if quote is None:
            raise NotFound()
        quotes.expire_quote(quote.pk)
        audit.record("pricing.quote_expired", request=request, target=quote)
        quote = PriceQuote.objects.prefetch_related("items__variant", "items__components").get(pk=quote.pk)
        return Response(quote_out(quote, full=True))
