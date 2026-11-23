"""Part G: missing / stale rates, fallback, provider health, timestamp validation, manual overrides, history."""

import json
from datetime import timedelta
from decimal import Decimal as D

import pytest
from django.utils import timezone

from apps.common.exceptions import BusinessRuleViolation
from apps.currencies.errors import ProviderError, RateError
from apps.currencies.models import Currency, ExchangeRate, ExchangeRateSource
from apps.currencies.providers import http_json
from apps.currencies.providers.base import ExchangeRateProvider, RateObservation
from apps.currencies.providers.registry import use_provider
from apps.currencies.services import health, manual, refresh
from apps.currencies.services.resolver import resolve_rate
from tests.pricing_helpers import rate, route

pytestmark = pytest.mark.django_db


def test_reference_currencies_are_seeded():
    assert set(Currency.objects.values_list("code", flat=True)) >= {"IRR", "IRT", "TRY", "AED", "USD", "EUR"}
    irt = Currency.objects.get(code="IRT")
    assert irt.base_currency_id == "IRR" and irt.base_factor == D("10") and irt.is_display_unit


# ------------------------------------------------------------------------------ resolution
def test_no_route_means_no_rate_not_a_guess():
    rate("TRY", "15000")  # a nominal rate exists, but nobody configured it as THE rate
    with pytest.raises(RateError) as e:
        resolve_rate("TRY", "IRR", "purchase")
    assert e.value.code == "no_rate_route"


def test_route_without_rows_is_missing():
    route("TRY")
    with pytest.raises(RateError) as e:
        resolve_rate("TRY")
    assert e.value.code == "rate_missing"


def test_disabled_route_and_unknown_currency():
    route("TRY", is_enabled=False)
    with pytest.raises(RateError, match="disabled"):
        resolve_rate("TRY")
    with pytest.raises(RateError) as e:
        resolve_rate("XXX")
    assert e.value.code == "unknown_currency"


def test_stale_rate_is_refused_by_default():
    route("TRY", max_age=3600)
    rate("TRY", "15000", age_seconds=7200)
    with pytest.raises(RateError) as e:
        resolve_rate("TRY")
    assert e.value.code == "rate_stale"


def test_stale_fallback_is_flagged_when_the_route_allows_it():
    route("TRY", max_age=3600, allow_stale_fallback=True)
    rate("TRY", "15000", age_seconds=7200)
    resolved = resolve_rate("TRY")
    assert resolved.is_stale and resolved.rate == D("15000")


def test_fresh_boundary_is_exclusive():
    now = timezone.now()
    route("TRY", max_age=3600)
    rate("TRY", "15000", age_seconds=3599, now=now)
    assert not resolve_rate("TRY", at=now).is_stale
    with pytest.raises(RateError):
        resolve_rate("TRY", at=now + timedelta(seconds=2))


def test_fallback_to_the_next_source_when_the_first_is_stale():
    route("USD", sources=("bureau", "bank"), max_age=3600)
    rate("USD", "560000", src="bureau", age_seconds=9000)
    rate("USD", "550000", src="bank", age_seconds=10)
    resolved = resolve_rate("USD")
    assert resolved.source_code == "bank" and resolved.is_fallback and resolved.rate == D("550000")


def test_priority_decides_not_recency_or_size():
    route("USD", sources=("bank", "bureau"))
    rate("USD", "550000", src="bank", age_seconds=1000)
    rate("USD", "560000", src="bureau", age_seconds=1)
    assert resolve_rate("USD").source_code == "bank"


def test_disabled_source_is_skipped():
    route("USD", sources=("bank", "bureau"))
    rate("USD", "550000", src="bank")
    rate("USD", "560000", src="bureau")
    ExchangeRateSource.objects.filter(code="bank").update(is_enabled=False)
    assert resolve_rate("USD").source_code == "bureau"


def test_quarantined_rates_are_never_used():
    route("USD")
    rate("USD", "999999", status="quarantined")
    with pytest.raises(RateError):
        resolve_rate("USD")


def test_unit_conversion_toman_to_rial_needs_no_route():
    assert resolve_rate("IRT", "IRR").rate == D("10")
    assert resolve_rate("IRR", "IRR").rate == D("1")


# ------------------------------------------------------------------------------ manual rates, history, immutability
def test_manual_rate_needs_a_reason_and_a_positive_number(make_staff):
    staff = make_staff(superuser=True)
    with pytest.raises(BusinessRuleViolation) as e:
        manual.set_manual_rate(base="USD", quote="IRR", rate="550000", actor=staff, note=" ")
    assert e.value.get_codes() == "note_required"
    for bad in ("0", "-1", "abc", "NaN"):
        with pytest.raises(BusinessRuleViolation):
            manual.set_manual_rate(base="USD", quote="IRR", rate=bad, actor=staff, note="why")


def test_manual_rate_as_primary_source_overrides_then_falls_through_when_expired(make_staff):
    staff = make_staff(superuser=True)
    route("USD", sources=("manual", "bureau"), max_age=3600)
    rate("USD", "550000", src="bureau")
    row = manual.set_manual_rate(
        base="USD",
        quote="IRR",
        rate="580000",
        actor=staff,
        note="agent quote 22 Nov",
        valid_until=timezone.now() + timedelta(minutes=10),
    )
    assert resolve_rate("USD").rate == D("580000") and resolve_rate("USD").is_manual
    later = timezone.now() + timedelta(minutes=30)
    rate("USD", "551000", src="bureau", now=later)  # a newer automatic rate exists by then
    fallen = resolve_rate("USD", at=later)
    assert fallen.source_code == "bureau" and fallen.rate == D("551000") and fallen.is_fallback
    assert row.is_manual and row.created_by == staff


def test_history_is_append_only():
    row = rate("USD", "550000")
    with pytest.raises(RuntimeError):
        row.rate = D("1")
        row.save()
    with pytest.raises(RuntimeError):
        row.delete()
    row.status = "quarantined"
    row.save(update_fields=["status"])  # review decisions are allowed
    rate("USD", "560000")
    assert ExchangeRate.objects.filter(base_id="USD").count() == 2


# ------------------------------------------------------------------------------ provider refresh + validation
class Scripted(ExchangeRateProvider):
    key = "scripted"
    script: list = []

    def fetch(self, src):
        item = Scripted.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def fixture_source(**extra):
    return ExchangeRateSource.objects.create(code="feed", name="feed", provider_key="fixture", kind="market", **extra)


def obs(base="USD", value="550000", as_of=None, quote="IRR"):
    return RateObservation(base, quote, D(value), as_of if as_of is not None else timezone.now())


def run_refresh(src, observations):
    Scripted.script = [observations]
    with use_provider("fixture", Scripted):
        return refresh.refresh_source(src)


def test_refresh_accepts_valid_observations_and_updates_health():
    src = fixture_source()
    log = run_refresh(src, [obs(), obs("EUR", "600000")])
    src.refresh_from_db()
    assert log.ok and log.n_accepted == 2 and src.consecutive_failures == 0
    assert health.source_health(src) == "healthy"
    assert ExchangeRate.objects.filter(source=src).count() == 2


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"as_of": timezone.now() + timedelta(hours=1)}, "timestamp_in_future"),
        ({"as_of": timezone.now() - timedelta(days=2)}, "observation_too_old"),
        ({"value": "0"}, "rate_not_positive"),
        ({"value": "-5"}, "rate_not_positive"),
        ({"base": "ZZZ"}, "unknown_or_identical_currency"),
        ({"base": "IRR"}, "unknown_or_identical_currency"),
    ],
)
def test_invalid_observations_are_rejected_with_a_reason(kwargs, reason):
    src = fixture_source()
    log = run_refresh(src, [obs(**kwargs)])
    assert not log.ok and log.n_rejected == 1 and log.details[0]["reason"] == reason
    assert not ExchangeRate.objects.exists()


def test_missing_and_naive_timestamps_are_rejected():
    src = fixture_source()
    Scripted.script = [
        [
            RateObservation("USD", "IRR", D("1"), None),
            RateObservation("EUR", "IRR", D("1"), timezone.now().replace(tzinfo=None)),
        ]
    ]
    with use_provider("fixture", Scripted):
        log = refresh.refresh_source(src)
    assert [d["reason"] for d in log.details] == ["timestamp_missing", "timestamp_naive"]


def test_big_jump_is_quarantined_not_used():
    src = fixture_source(max_jump_percent=D("10"))
    run_refresh(src, [obs(value="550000")])
    log = run_refresh(src, [obs(value="800000", as_of=timezone.now() + timedelta(seconds=5))])
    assert log.n_quarantined == 1 and not log.ok
    route("USD", sources=("feed",))
    assert resolve_rate("USD").rate == D("550000")  # the quarantined value is ignored


def test_older_than_recorded_observation_is_rejected():
    src = fixture_source()
    run_refresh(src, [obs(as_of=timezone.now())])
    log = run_refresh(src, [obs(as_of=timezone.now() - timedelta(hours=1))])
    assert log.details[0]["reason"] == "older_than_recorded"


def test_provider_failures_degrade_then_fail_health_and_recover():
    src = fixture_source(poll_interval_seconds=600)
    Scripted.script = [ProviderError("http_503", "down")] * 3 + [[obs()]]
    with use_provider("fixture", Scripted):
        refresh.refresh_source(src)
        src.refresh_from_db()
        assert health.source_health(src) == "degraded" and src.last_error == "http_503"
        refresh.refresh_source(src)
        refresh.refresh_source(src)
        src.refresh_from_db()
        assert src.consecutive_failures == 3 and health.source_health(src) == "failing"
        refresh.refresh_source(src)
    src.refresh_from_db()
    assert src.consecutive_failures == 0 and health.source_health(src) == "healthy"


def test_adapter_crash_is_contained():
    src = fixture_source()
    Scripted.script = [RuntimeError("boom")]
    with use_provider("fixture", Scripted):
        log = refresh.refresh_source(src)
    assert not log.ok and log.error_code == "adapter_error"


def test_unpolled_source_with_old_attempt_is_overdue_degraded():
    src = fixture_source(poll_interval_seconds=60)
    run_refresh(src, [obs()])
    src.refresh_from_db()
    assert health.source_health(src, now=timezone.now() + timedelta(minutes=10)) == "degraded"
    src.is_enabled = False
    assert health.source_health(src) == "disabled"


def test_due_sources_follow_their_own_interval():
    src = fixture_source(poll_interval_seconds=600)
    assert src in list(refresh.due_sources())
    run_refresh(src, [obs()])
    src.refresh_from_db()
    assert src not in list(refresh.due_sources())
    assert src in list(refresh.due_sources(now=timezone.now() + timedelta(seconds=601)))
    assert ExchangeRateSource.objects.get(code="manual") not in list(refresh.due_sources())


def test_refresh_task_runs_due_sources():
    from apps.currencies import tasks

    fixture_source(config={"rates": [{"base": "USD", "quote": "IRR", "rate": "550000", "as_of": "now"}]})
    assert tasks.refresh_due.apply().get() == ["feed"]
    assert ExchangeRate.objects.filter(base_id="USD").count() == 1


def test_fixture_provider_is_refused_when_disabled(settings):
    settings.CURRENCY_ALLOW_FIXTURE_PROVIDER = False
    src = fixture_source(config={"rates": [{"base": "USD", "quote": "IRR", "rate": "1", "as_of": "now"}]})
    log = refresh.refresh_source(src)
    assert not log.ok and log.error_code == "fixture_not_allowed"


# ------------------------------------------------------------------------------ http json adapter (scripted transport)
def http_source(**cfg):
    config = {
        "url": "https://rates.example.test/latest",
        "pairs": [{"base": "USD", "quote": "IRR", "path": "rates.USD"}],
        "as_of_path": "ts",
        "as_of_format": "epoch",
        **cfg,
    }
    return ExchangeRateSource.objects.create(
        code="http", name="http", provider_key="http_json", kind="official", config=config
    )


@pytest.fixture
def transport():
    calls = {}

    def install(status=200, body=None):
        def fn(url, headers, timeout):
            calls.update(url=url, headers=headers)
            return status, body if isinstance(body, str) else json.dumps(body)

        previous = http_json.set_transport(fn)
        return calls, previous

    yield install
    http_json.set_transport(None)


def test_http_adapter_parses_decimals_without_float(transport, settings):
    settings.CURRENCY_HTTP_ALLOWED_HOSTS = ["rates.example.test"]
    now = int(timezone.now().timestamp())
    transport(body='{"rates": {"USD": 550000.1234567}, "ts": ' + str(now) + "}")
    log = refresh.refresh_source(http_source())
    assert log.ok
    assert ExchangeRate.objects.get().rate == D("550000.1234567")


def test_http_adapter_blocks_hosts_outside_the_allow_list(transport, settings):
    settings.CURRENCY_HTTP_ALLOWED_HOSTS = []
    transport(body="{}")
    log = refresh.refresh_source(http_source())
    assert log.error_code == "host_not_allowed"
    settings.CURRENCY_HTTP_ALLOWED_HOSTS = ["rates.example.test"]
    src = ExchangeRateSource.objects.get(code="http")
    src.config = {**src.config, "url": "http://rates.example.test/x"}
    src.save()
    assert refresh.refresh_source(src).error_code == "host_not_allowed"


def test_http_adapter_requires_a_timestamp_unless_trusted(transport, settings):
    settings.CURRENCY_HTTP_ALLOWED_HOSTS = ["rates.example.test"]
    transport(body={"rates": {"USD": "550000"}})
    src = http_source(as_of_path="")
    log = refresh.refresh_source(src)
    assert not log.ok and log.details[0]["reason"] == "timestamp_missing"
    src.config = {**src.config, "trust_fetch_time": True}
    src.save()
    assert refresh.refresh_source(src).ok


@pytest.mark.parametrize(("status", "code"), [(503, "http_503"), (429, "http_429"), (404, "http_404")])
def test_http_adapter_maps_failures(transport, settings, status, code):
    settings.CURRENCY_HTTP_ALLOWED_HOSTS = ["rates.example.test"]
    transport(status=status, body="{}")
    assert refresh.refresh_source(http_source()).error_code == code


def test_http_adapter_secret_comes_from_the_environment_only(transport, settings, monkeypatch):
    settings.CURRENCY_HTTP_ALLOWED_HOSTS = ["rates.example.test"]
    calls, _ = transport(body={"rates": {"USD": "1"}, "ts": int(timezone.now().timestamp())})
    src = http_source(auth_header="Authorization", auth_prefix="Bearer ")
    assert refresh.refresh_source(src).error_code == "credentials_missing"
    src.credential_ref = "acme"
    src.save()
    monkeypatch.setenv("CURRENCY_CRED_ACME", "s3cret")
    assert refresh.refresh_source(src).ok and calls["headers"]["Authorization"] == "Bearer s3cret"
    assert "s3cret" not in json.dumps(src.config)
