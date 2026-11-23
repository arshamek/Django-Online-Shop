"""Currency administration API. Needs a staff user with `accounts.pricing_manage`. Writes are audited."""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
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
from apps.common.api.pagination import StandardPagination
from apps.common.exceptions import BusinessRuleViolation

from ..errors import RateError
from ..models import (
    Currency,
    ExchangeRate,
    ExchangeRateSource,
    ProviderKey,
    RateRoute,
    RateRouteSource,
    RateStatus,
)
from ..services import health, manual, refresh
from ..services.resolver import resolve_rate
from .public import plain

PERM = "accounts.pricing_manage"
TAG = ["staff-currencies"]


class StaffView(NoStoreMixin, APIView):
    permission_classes = [IsAuthenticated, IsStaff, HasStaffPermission]
    parser_classes = [JSONParser]
    required_permissions = {"GET": PERM, "POST": PERM, "PATCH": PERM, "DELETE": PERM}


def currency_out(c):
    return {
        "code": c.code,
        "name": c.name,
        "symbol": c.symbol,
        "decimal_places": c.decimal_places,
        "is_source": c.is_source,
        "is_display": c.is_display,
        "is_active": c.is_active,
        "sort_order": c.sort_order,
        "unit_of": {"currency": c.base_currency_id, "factor": plain(c.base_factor)} if c.base_currency_id else None,
    }


def source_out(s, now=None):
    return {
        "code": s.code,
        "name": s.name,
        "provider": s.provider_key,
        "kind": s.kind,
        "description": s.description,
        "config": s.config,
        "credential_ref": s.credential_ref,
        "is_enabled": s.is_enabled,
        "poll_interval_seconds": s.poll_interval_seconds,
        "max_observation_age_seconds": s.max_observation_age_seconds,
        "max_jump_percent": None if s.max_jump_percent is None else str(s.max_jump_percent),
        "health": health.source_health(s, now),
        "last_attempt_at": s.last_attempt_at,
        "last_success_at": s.last_success_at,
        "last_failure_at": s.last_failure_at,
        "consecutive_failures": s.consecutive_failures,
        "last_error": s.last_error,
        "live_verified": False,
    }


def rate_out(r):
    return {
        "id": str(r.public_id),
        "source": r.source.code,
        "base": r.base_id,
        "quote": r.quote_id,
        "rate": str(r.rate),
        "rate_at": r.rate_at,
        "fetched_at": r.fetched_at,
        "valid_until": r.valid_until,
        "status": r.status,
        "status_reason": r.status_reason,
        "is_manual": r.is_manual,
        "note": r.note,
        "created_by": r.created_by.email if r.created_by_id and r.created_by.email else None,
    }


def route_out(route):
    return {
        "id": route.pk,
        "base": route.base_id,
        "quote": route.quote_id,
        "purpose": route.purpose,
        "max_age_seconds": route.max_age_seconds,
        "allow_stale_fallback": route.allow_stale_fallback,
        "is_enabled": route.is_enabled,
        "note": route.note,
        "sources": [
            {"code": link.source.code, "priority": link.priority}
            for link in route.route_sources.select_related("source")
        ],
    }


# ------------------------------------------------------------------------------------------------ currencies
class CurrencyListView(StaffView):
    @extend_schema(tags=TAG, summary="All currencies", responses={200: dict})
    def get(self, request):
        return Response({"results": [currency_out(c) for c in Currency.objects.all()]})


class CurrencyDetailView(StaffView):
    class In(serializers.Serializer):
        name = serializers.CharField(max_length=60, required=False)
        symbol = serializers.CharField(max_length=8, required=False, allow_blank=True)
        is_source = serializers.BooleanField(required=False)
        is_display = serializers.BooleanField(required=False)
        is_active = serializers.BooleanField(required=False)
        sort_order = serializers.IntegerField(min_value=0, required=False)

    @extend_schema(tags=TAG, summary="Enable/disable or relabel a currency", request=In, responses={200: dict})
    def patch(self, request, code):
        currency = Currency.objects.filter(code=code.upper()).first()
        if currency is None:
            raise NotFound()
        data = self.In(data=request.data)
        data.is_valid(raise_exception=True)
        if currency.code in ("IRR",) and data.validated_data.get("is_active") is False:
            raise BusinessRuleViolation(
                "IRR is the accounting currency and cannot be disabled.", code="accounting_currency"
            )
        for key, value in data.validated_data.items():
            setattr(currency, key, value)
        currency.save()
        audit.record(
            "currencies.currency_updated", request=request, target=("currency", currency.code), **data.validated_data
        )
        return Response(currency_out(currency))


# ------------------------------------------------------------------------------------------------ sources
class SourceIn(serializers.Serializer):
    code = serializers.SlugField(max_length=40)
    name = serializers.CharField(max_length=120)
    provider = serializers.ChoiceField(choices=ProviderKey.choices, source="provider_key")
    kind = serializers.CharField(max_length=10)
    description = serializers.CharField(required=False, allow_blank=True)
    config = serializers.JSONField(required=False)
    credential_ref = serializers.CharField(max_length=40, required=False, allow_blank=True)
    is_enabled = serializers.BooleanField(required=False)
    poll_interval_seconds = serializers.IntegerField(min_value=30, max_value=86400, required=False)
    max_observation_age_seconds = serializers.IntegerField(min_value=60, required=False)
    max_jump_percent = serializers.DecimalField(
        max_digits=7, decimal_places=2, min_value=Decimal("0.01"), required=False, allow_null=True
    )


class SourceListView(StaffView):
    @extend_schema(
        tags=TAG,
        operation_id="staff_currencies_sources_list",
        summary="Rate sources with health",
        responses={200: dict},
    )
    def get(self, request):
        now = timezone.now()
        return Response({"results": [source_out(s, now) for s in ExchangeRateSource.objects.all()]})

    @extend_schema(tags=TAG, summary="Add a rate source", request=SourceIn, responses={201: dict})
    def post(self, request):
        data = SourceIn(data=request.data)
        data.is_valid(raise_exception=True)
        if ExchangeRateSource.objects.filter(code=data.validated_data["code"]).exists():
            raise BusinessRuleViolation("A source with this code exists.", code="duplicate_code")
        _check_config(data.validated_data)
        source = ExchangeRateSource.objects.create(**data.validated_data)
        audit.record("currencies.source_created", request=request, target=source, code=source.code)
        return Response(source_out(source), status=201)


def _check_config(values):
    kind = values.get("kind", "")
    from ..models import RateKind

    if kind and kind not in RateKind.values:
        raise BusinessRuleViolation("Unknown rate kind.", code="invalid_kind")
    config = values.get("config") or {}
    if "api_key" in config or "secret" in config or "password" in config:
        raise BusinessRuleViolation(
            "Secrets must not be stored in the configuration; use credential_ref.", code="secret_in_config"
        )


class SourceDetailView(StaffView):
    class Patch(SourceIn):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            for f in self.fields.values():
                f.required = False

    def _get(self, code):
        source = ExchangeRateSource.objects.filter(code=code).first()
        if source is None:
            raise NotFound()
        return source

    @extend_schema(tags=TAG, summary="One source", responses={200: dict})
    def get(self, request, code):
        return Response(source_out(self._get(code)))

    @extend_schema(tags=TAG, summary="Configure / enable / disable a source", request=Patch, responses={200: dict})
    def patch(self, request, code):
        source = self._get(code)
        data = self.Patch(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        values.pop("code", None)
        _check_config(values)
        for key, value in values.items():
            setattr(source, key, value)
        source.save()
        audit.record("currencies.source_updated", request=request, target=source, changed=sorted(values))
        return Response(source_out(source))


class SourceRefreshView(StaffView):
    @extend_schema(tags=TAG, summary="Poll a source now", request=None, responses={200: dict})
    def post(self, request, code):
        source = ExchangeRateSource.objects.filter(code=code).first()
        if source is None:
            raise NotFound()
        if not source.is_polled or not source.is_enabled:
            raise BusinessRuleViolation("Only enabled, pollable sources can be refreshed.", code="not_pollable")
        log = refresh.refresh_source(source)
        audit.record("currencies.source_refreshed", request=request, target=source, ok=log.ok)
        return Response(
            {
                "ok": log.ok,
                "received": log.n_received,
                "accepted": log.n_accepted,
                "quarantined": log.n_quarantined,
                "rejected": log.n_rejected,
                "error": log.error_code,
                "details": log.details,
            }
        )


# ------------------------------------------------------------------------------------------------ routes
class RouteIn(serializers.Serializer):
    base = serializers.SlugRelatedField(slug_field="code", queryset=Currency.objects.all())
    quote = serializers.SlugRelatedField(
        slug_field="code",
        queryset=Currency.objects.all(),
        required=False,
        default=lambda: Currency.objects.get(pk="IRR"),
    )
    purpose = serializers.ChoiceField(choices=[("purchase", ""), ("display", "")])
    max_age_seconds = serializers.IntegerField(min_value=60)
    allow_stale_fallback = serializers.BooleanField(required=False)
    is_enabled = serializers.BooleanField(required=False)
    note = serializers.CharField(max_length=300, required=False, allow_blank=True)
    sources = serializers.ListField(child=serializers.DictField(), allow_empty=False)

    def validate_sources(self, value):
        out = []
        for item in value:
            source = ExchangeRateSource.objects.filter(code=item.get("code")).first()
            if source is None:
                raise serializers.ValidationError(f"Unknown source {item.get('code')!r}.")
            out.append((source, int(item.get("priority", 10))))
        return out


def _save_route(route, data):
    sources = data.pop("sources", None)
    for key, value in data.items():
        setattr(route, key, value)
    route.save()
    if sources is not None:
        route.route_sources.all().delete()
        RateRouteSource.objects.bulk_create([RateRouteSource(route=route, source=s, priority=p) for s, p in sources])
    return route


class RouteListView(StaffView):
    @extend_schema(
        tags=TAG,
        operation_id="staff_currencies_routes_list",
        summary="Which sources provide each rate, in which order",
        responses={200: dict},
    )
    def get(self, request):
        return Response({"results": [route_out(r) for r in RateRoute.objects.select_related("base", "quote")]})

    @extend_schema(tags=TAG, summary="Configure the rate source for a currency", request=RouteIn, responses={201: dict})
    def post(self, request):
        data = RouteIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = dict(data.validated_data)
        if RateRoute.objects.filter(base=v["base"], quote=v["quote"], purpose=v["purpose"]).exists():
            raise BusinessRuleViolation(
                "A route for this pair and purpose exists; update it instead.", code="duplicate_route"
            )
        with transaction.atomic():
            route = _save_route(RateRoute(), v)
            audit.record("currencies.route_created", request=request, target=("rate_route", route.pk), pair=str(route))
        return Response(route_out(route), status=201)


class RouteDetailView(StaffView):
    def _get(self, pk):
        route = RateRoute.objects.filter(pk=pk).first()
        if route is None:
            raise NotFound()
        return route

    @extend_schema(tags=TAG, summary="One route", responses={200: dict})
    def get(self, request, pk):
        return Response(route_out(self._get(pk)))

    @extend_schema(
        tags=TAG,
        summary="Change a route (sources, order, maximum age, stale fallback)",
        request=RouteIn,
        responses={200: dict},
    )
    def patch(self, request, pk):
        route = self._get(pk)
        data = RouteIn(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            _save_route(route, dict(data.validated_data))
            audit.record(
                "currencies.route_updated",
                request=request,
                target=("rate_route", route.pk),
                changed=sorted(data.validated_data),
            )
        return Response(route_out(route))

    @extend_schema(tags=TAG, summary="Delete a route (the pair then has no rate)", responses={204: None})
    def delete(self, request, pk):
        route = self._get(pk)
        audit.record("currencies.route_deleted", request=request, target=("rate_route", route.pk), pair=str(route))
        route.delete()
        return Response(status=204)


# ------------------------------------------------------------------------------------------------ rates
class RateHistoryView(StaffView):
    class ManualIn(serializers.Serializer):
        base = serializers.SlugRelatedField(slug_field="code", queryset=Currency.objects.all())
        quote = serializers.SlugRelatedField(
            slug_field="code",
            queryset=Currency.objects.all(),
            required=False,
            default=lambda: Currency.objects.get(pk="IRR"),
        )
        rate = serializers.CharField(max_length=40)
        note = serializers.CharField(max_length=300)
        valid_until = serializers.DateTimeField(required=False, allow_null=True)
        source = serializers.SlugField(required=False)

    @extend_schema(
        tags=TAG, summary="Historical rates (filter: base, quote, source, status, from, to)", responses={200: dict}
    )
    def get(self, request):
        qs = ExchangeRate.objects.select_related("source", "created_by").order_by("-rate_at", "-id")
        p = request.query_params
        for field, lookup in (
            ("base", "base_id"),
            ("quote", "quote_id"),
            ("source", "source__code"),
            ("status", "status"),
        ):
            if p.get(field):
                qs = qs.filter(**{lookup: p[field]})
        for field, lookup in (("from", "rate_at__gte"), ("to", "rate_at__lte")):
            if p.get(field):
                parsed = parse_datetime(p[field])
                if parsed is None:
                    raise BusinessRuleViolation(f"Invalid {field} timestamp.", code="invalid_filter")
                qs = qs.filter(**{lookup: parsed})
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response([rate_out(r) for r in page])

    @extend_schema(tags=TAG, summary="Set a manual exchange rate", request=ManualIn, responses={201: dict})
    def post(self, request):
        data = self.ManualIn(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        source = None
        if v.get("source"):
            source = ExchangeRateSource.objects.filter(code=v["source"]).first()
            if source is None:
                raise NotFound()
        row = manual.set_manual_rate(
            base=v["base"].code,
            quote=v["quote"].code,
            rate=v["rate"],
            actor=request.user,
            note=v["note"],
            valid_until=v.get("valid_until"),
            source=source,
            request=request,
        )
        return Response(rate_out(row), status=201)


class RateReviewView(StaffView):
    class In(serializers.Serializer):
        decision = serializers.ChoiceField(choices=["accept", "reject"])
        reason = serializers.CharField(max_length=200)

    @extend_schema(tags=TAG, summary="Accept or reject a quarantined rate", request=In, responses={200: dict})
    def post(self, request, id):
        row = ExchangeRate.objects.select_related("source").filter(public_id=id).first()
        if row is None:
            raise NotFound()
        if row.status != RateStatus.QUARANTINED:
            raise BusinessRuleViolation("Only quarantined rates can be reviewed.", code="not_quarantined")
        data = self.In(data=request.data)
        data.is_valid(raise_exception=True)
        row.status = RateStatus.ACCEPTED if data.validated_data["decision"] == "accept" else RateStatus.REJECTED
        row.status_reason = f"reviewed: {data.validated_data['reason']}"[:200]
        row.save(update_fields=["status", "status_reason"])
        audit.record("currencies.rate_reviewed", request=request, target=row, decision=data.validated_data["decision"])
        return Response(rate_out(row))


class HealthView(StaffView):
    @extend_schema(tags=TAG, summary="Source health and the state of every configured route", responses={200: dict})
    def get(self, request):
        now = timezone.now()
        routes = []
        for route in RateRoute.objects.all():
            try:
                r = resolve_rate(route.base_id, route.quote_id, route.purpose, now)
                state = {
                    "state": "stale_fallback" if r.is_stale else "ok",
                    "source": r.source_code,
                    "rate": str(r.rate),
                    "rate_at": r.rate_at,
                    "age_seconds": int((now - r.rate_at).total_seconds()) if r.rate_at else None,
                    "is_fallback": r.is_fallback,
                }
            except RateError as exc:
                state = {"state": exc.code}
            routes.append({"pair": f"{route.base_id}/{route.quote_id}", "purpose": route.purpose, **state})
        missing = [
            c
            for c in Currency.objects.filter(is_source=True, is_active=True).values_list("code", flat=True)
            if not RateRoute.objects.filter(base=c, quote="IRR", purpose="purchase", is_enabled=True).exists()
        ]
        return Response(
            {
                "sources": [source_out(s, now) for s in ExchangeRateSource.objects.all()],
                "routes": routes,
                "source_currencies_without_purchase_route": missing,
            }
        )
