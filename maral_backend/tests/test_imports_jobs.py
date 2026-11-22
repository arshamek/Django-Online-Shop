from dataclasses import replace
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.common.exceptions import BusinessRuleViolation, Conflict, DomainError
from apps.imports import tasks
from apps.imports.models import ImportJob, ImportLog, JobStatus, JobTrigger, JobType
from apps.imports.services import jobs
from apps.imports.services.logs import log
from apps.product_sources.models import ExternalProduct, IntegrationType
from apps.product_sources.providers.dto import RejectedItem
from apps.product_sources.providers.errors import (
    ProviderNotAuthorized,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from apps.product_sources.providers.mock import InMemoryProvider
from apps.product_sources.providers.registry import use_provider
from apps.product_sources.providers.trendyol import TrendyolProvider
from tests import catalog_factories as f
from tests.imports_helpers import ScriptedTransport, make_product, make_variant, run_job_sync, world

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_enqueue")]


def provider_for(products=(), **kw):
    holder = {}

    def factory(source, **kwargs):
        holder["p"] = holder.get("p") or InMemoryProvider(source, products, **kw)
        return holder["p"]

    return factory, holder


def products(n=3, **extra):
    return [
        make_product(f"P{i}", title=f"Acme Shoe {i}", variants=[make_variant(f"V{i}", size="40")], **extra)
        for i in range(1, n + 1)
    ]


@pytest.fixture
def src():
    return world()[0]


def start(src, job_type=JobType.FULL, **kw):
    return jobs.create_job(source=src, job_type=job_type, **kw)[0]


# ------------------------------------------------------------------ happy path, summary, progress, logs
def test_full_import_progress_summary_and_logs(src):
    factory, _ = provider_for(products(3))
    with use_provider("trendyol", factory):
        job = run_job_sync(start(src))
    assert job.status == JobStatus.SUCCEEDED and (job.n_seen, job.n_created, job.n_failed) == (3, 3, 0)
    assert job.progress_percent == 100.0 and job.n_total == 3 and job.finished_at and job.started_at
    assert job.summary["created"] == 3 and job.summary["products_by_status"]
    levels = set(job.logs.values_list("level", flat=True))
    assert "info" in levels and job.logs.filter(message="Job succeeded.").exists()


def test_empty_catalogue_is_a_successful_job(src):
    factory, _ = provider_for([])
    with use_provider("trendyol", factory):
        job = run_job_sync(start(src))
    assert job.status == JobStatus.SUCCEEDED and job.n_seen == 0


def test_log_context_is_redacted_and_logs_are_append_only(src):
    job = start(src)
    entry = log(job, "info", "hello", api_key="SUPERSECRET", note="fine")
    assert "SUPERSECRET" not in str(entry.context) and entry.context["note"] == "fine"
    entry.message = "changed"
    with pytest.raises(RuntimeError):
        entry.save()


# ------------------------------------------------------------------ partial imports
def test_one_bad_product_does_not_sink_the_job(src):
    items = products(3)
    items[1] = replace(items[1], title="")
    factory, _ = provider_for(items, rejected=[RejectedItem("BAD", "invalid_item", "malformed")])
    with use_provider("trendyol", factory):
        job = run_job_sync(start(src))
    assert job.status == JobStatus.PARTIALLY_FAILED and (job.n_created, job.n_failed) == (2, 2)
    assert job.summary["errors_by_code"] == {"missing_title": 1, "invalid_item": 1}
    assert ExternalProduct.objects.count() == 2


def test_every_item_failing_marks_the_job_failed(src):
    factory, _ = provider_for([replace(p, title="") for p in products(2)])
    with use_provider("trendyol", factory):
        assert run_job_sync(start(src)).status == JobStatus.FAILED


def test_unexpected_error_in_one_item_is_isolated(src, monkeypatch):
    real = jobs.process_product

    def flaky(product, **kw):
        if product.external_id == "P2":
            raise RuntimeError("boom with secret details")
        return real(product, **kw)

    monkeypatch.setattr(jobs, "process_product", flaky)
    factory, _ = provider_for(products(3))
    with use_provider("trendyol", factory):
        job = run_job_sync(start(src))
    assert job.status == JobStatus.PARTIALLY_FAILED
    item = job.items.get(external_id="P2")
    assert (item.error_code, item.error_message) == ("internal_error", "RuntimeError")  # type only, no payload
    assert not ImportLog.objects.filter(job=job, message__contains="secret").exists()


def test_duplicate_records_in_the_feed_are_imported_once(src):
    item = {
        "contentId": 1,
        "title": "Dup",
        "brand": {"name": "Acme"},
        "category": {"id": "c1", "name": "Shoes"},
        "variants": [
            {
                "variantId": 1,
                "price": {"salePrice": 10, "listPrice": 12},
                "stock": {"quantity": 1},
                "attributes": [{"attributeName": "Beden", "attributeValue": "40"}],
            }
        ],
    }
    transport = ScriptedTransport({"totalPages": 1, "totalElements": 2, "content": [item, item]})
    with use_provider("trendyol", lambda s, **kw: TrendyolProvider(s, transport=transport, mode="fixture")):
        job = run_job_sync(start(src))
    assert (job.n_seen, ExternalProduct.objects.count()) == (1, 1) and job.status == JobStatus.SUCCEEDED


# ------------------------------------------------------------------ provider failures, rate limits, timeouts, retries
def test_transient_failure_waits_then_resumes_without_repeating_work(src, settings):
    settings.IMPORTS_PAGE_SIZE = 1

    def page(cid, last):
        return {
            "totalPages": 2,
            "totalElements": 2,
            "content": [
                {
                    "contentId": cid,
                    "title": f"Acme {cid}",
                    "brand": {"name": "Acme"},
                    "category": {"id": "c1", "name": "S"},
                    "variants": [
                        {
                            "variantId": cid,
                            "price": {"salePrice": 10, "listPrice": 12},
                            "stock": {"quantity": 1},
                            "attributes": [{"attributeName": "Beden", "attributeValue": "40"}],
                        }
                    ],
                }
            ],
        }

    transport = ScriptedTransport(page(1, False), ProviderTimeout("slow"), page(2, True))
    with use_provider("trendyol", lambda s, **kw: TrendyolProvider(s, transport=transport, mode="fixture")):
        job = start(src)
        first = jobs.run_slice(job.pk)
        job.refresh_from_db()
        assert first.state == "retry" and job.status == JobStatus.WAITING and job.attempt == 1
        assert job.cursor == "page:1" and job.n_created == 1 and job.resume_at > timezone.now()
        assert jobs.run_slice(job.pk).state == "retry"  # too early: the source is not contacted again
        assert len(transport.calls) == 2
        ImportJob.objects.filter(pk=job.pk).update(resume_at=timezone.now() - timedelta(seconds=1))
        jobs.run_slice(job.pk)
    job.refresh_from_db()
    assert [c[1].get("page") for c in transport.calls] == [0, 1, 1]
    assert job.status == JobStatus.SUCCEEDED and job.n_created == 2 and job.items.count() == 2


def test_rate_limit_waits_for_exactly_what_the_source_asked(src):
    factory, holder = provider_for(products(1))
    with use_provider("trendyol", factory):
        job = start(src)
        factory(src).fail_next("discover", ProviderRateLimited("429", retry_after=45))
        outcome = jobs.run_slice(job.pk)
    job.refresh_from_db()
    assert outcome.state == "retry" and outcome.retry_after == 45
    assert job.status == JobStatus.WAITING and job.attempt == 0  # waiting for permission is not a failure
    assert 40 < (job.resume_at - timezone.now()).total_seconds() <= 45
    assert job.error_code == "rate_limited"


def test_local_rate_limit_ceiling_pauses_the_job(src):
    from apps.product_sources.providers.ratelimit import SourceRateLimiter

    limiter = SourceRateLimiter("trendyol", 1, clock=lambda: 1_000_000.0)  # frozen clock: one request per minute
    with use_provider("trendyol", lambda s, **kw: InMemoryProvider(s, products(1), limiter=limiter, **kw)):
        job = start(src, JobType.BULK, params={"external_ids": ["P1", "P2"]})
        outcome = jobs.run_slice(job.pk)  # P1 uses the only request in this minute, P2 must wait
    job.refresh_from_db()
    assert outcome.state == "retry" and job.status == JobStatus.WAITING and job.n_created == 1


@pytest.mark.parametrize("error", [ProviderTimeout("t"), ProviderUnavailable("u")])
def test_network_failures_back_off_then_give_up(src, settings, error):
    settings.IMPORTS_MAX_ATTEMPTS = 2
    factory, _ = provider_for(products(1))
    with use_provider("trendyol", factory):
        job = start(src)
        factory(src).fail_next("discover", error, error, error)
        delays = []
        for _ in range(3):
            ImportJob.objects.filter(pk=job.pk, status=JobStatus.WAITING).update(resume_at=None)
            outcome = jobs.run_slice(job.pk)
            delays.append(outcome.retry_after)
    job.refresh_from_db()
    assert delays[:2] == [30.0, 60.0]  # exponential back-off
    assert job.status == JobStatus.FAILED and job.error_code == "retries_exhausted"


def test_authorisation_failure_is_not_retried(src):
    factory, _ = provider_for(products(1))
    with use_provider("trendyol", factory):
        job = start(src)
        factory(src).fail_next("discover", ProviderNotAuthorized("HTTP 403"))
        outcome = jobs.run_slice(job.pk)
        job.refresh_from_db()
        assert outcome.state == "done" and job.status == JobStatus.FAILED and job.error_code == "not_authorized"
        assert len(factory(src).calls) == 1


def test_live_mode_refuses_unauthorised_sources_before_creating_a_job(src, settings):
    settings.IMPORTS_PROVIDER_MODE = "live"
    with pytest.raises(BusinessRuleViolation) as info:
        start(src)
    assert info.value.detail.code == "source_not_authorized" and ImportJob.objects.count() == 0
    src.integration_type, src.terms_note = IntegrationType.OFFICIAL_API, "Agreement 7"
    src.save()
    settings.IMPORTS_CREDENTIALS = {"trendyol": {}}
    with pytest.raises(BusinessRuleViolation) as info:
        start(src)
    assert info.value.detail.code == "source_not_configured"


# ------------------------------------------------------------------ cancellation, retry, recovery
def test_cancelling_a_queued_job_stops_it_immediately(src):
    factory, _ = provider_for(products(2))
    with use_provider("trendyol", factory):
        job = start(src)
        jobs.request_cancel(job)
        job.refresh_from_db()
        assert job.status == JobStatus.CANCELLED and jobs.run_slice(job.pk).state == "done"
        assert factory(src).calls == [] and ExternalProduct.objects.count() == 0
        with pytest.raises(Conflict) as info:
            jobs.request_cancel(job)
        assert info.value.detail.code == "job_finished"


def test_cancelling_a_running_job_keeps_finished_work_and_can_resume(src, monkeypatch):
    real = jobs.process_product
    seen = []

    def cancel_after_first(product, **kw):
        seen.append(product.external_id)
        result = real(product, **kw)
        if len(seen) == 1:
            ImportJob.objects.filter(source=src).update(cancel_requested=True)
        return result

    monkeypatch.setattr(jobs, "process_product", cancel_after_first)
    factory, _ = provider_for(products(3))
    with use_provider("trendyol", factory):
        job = run_job_sync(start(src))
        assert job.status == JobStatus.CANCELLED and job.n_created == 1 and ExternalProduct.objects.count() == 1
        monkeypatch.setattr(jobs, "process_product", real)
        jobs.retry_job(job)
        job = run_job_sync(job)
    assert job.status == JobStatus.SUCCEEDED and job.n_created == 3 and job.retry_count == 1
    assert job.items.count() == 3 and ExternalProduct.objects.count() == 3


def test_retry_repairs_failed_items_and_continues_from_the_cursor(src):
    items = products(3)
    broken = replace(items[1], title="")
    factory, holder = provider_for([items[0], broken, items[2]])
    with use_provider("trendyol", factory):
        job = run_job_sync(start(src))
        assert job.status == JobStatus.PARTIALLY_FAILED
        holder["p"].products["P2"] = items[1]  # the source fixed its data
        jobs.retry_job(job)
        job = run_job_sync(job)
    assert job.status == JobStatus.SUCCEEDED and (job.n_created, job.n_failed) == (3, 0)
    assert ExternalProduct.objects.count() == 3


def test_only_finished_unsuccessful_jobs_can_be_retried(src):
    factory, _ = provider_for(products(1))
    with use_provider("trendyol", factory):
        job = start(src)
        with pytest.raises(Conflict):
            jobs.retry_job(job)  # still queued
        job = run_job_sync(job)
        with pytest.raises(Conflict) as info:
            jobs.retry_job(job)  # succeeded
        assert info.value.detail.code == "job_not_retryable"


def test_job_runs_in_slices_and_resumes_from_its_checkpoint(src, settings):
    settings.IMPORTS_PAGE_SIZE, settings.IMPORTS_SLICE_SECONDS = 1, 0
    factory, _ = provider_for(products(3))
    with use_provider("trendyol", factory):
        job = start(src)
        states = []
        for _ in range(6):
            outcome = jobs.run_slice(job.pk)
            states.append(outcome.state)
            if outcome.state == "done":
                break
        job.refresh_from_db()
    assert states == ["continue", "continue", "done"] and job.status == JobStatus.SUCCEEDED and job.n_created == 3


def test_stalled_jobs_are_recovered_and_live_ones_left_alone(src, no_enqueue):
    old = timezone.now() - timedelta(minutes=60)
    stalled = start(src, JobType.SYNC)
    ImportJob.objects.filter(pk=stalled.pk).update(status=JobStatus.RUNNING, heartbeat_at=old)
    lost_wait = start(src, JobType.SINGLE, params={"external_id": "X"})
    ImportJob.objects.filter(pk=lost_wait.pk).update(status=JobStatus.WAITING, resume_at=old)
    live = start(src, JobType.BULK, params={"external_ids": ["Y"]})
    ImportJob.objects.filter(pk=live.pk).update(status=JobStatus.RUNNING, heartbeat_at=timezone.now())
    no_enqueue.clear()
    recovered = jobs.recover_stale()
    assert set(recovered) == {stalled.pk, lost_wait.pk}
    assert ImportJob.objects.get(pk=live.pk).status == JobStatus.RUNNING
    assert {pk for pk, _ in no_enqueue} == {stalled.pk, lost_wait.pk}
    factory, _ = provider_for([])
    with use_provider("trendyol", factory):
        assert jobs.run_slice(live.pk).state == "done"  # another worker owns it: nothing happens
    assert factory(src).calls == []


# ------------------------------------------------------------------ job types, idempotency, concurrency
def test_idempotency_key_returns_the_original_job(src):
    a, created_a = jobs.create_job(source=src, job_type=JobType.FULL, idempotency_key="k1")
    b, created_b = jobs.create_job(source=src, job_type=JobType.FULL, idempotency_key="k1")
    assert created_a and not created_b and a.pk == b.pk and ImportJob.objects.count() == 1


def test_only_one_exclusive_job_per_source_and_type(src):
    start(src)
    with pytest.raises(Conflict) as info:
        start(src)
    assert info.value.detail.code == "import_already_running"
    start(src, JobType.SINGLE, params={"external_id": "A"})
    start(src, JobType.SINGLE, params={"external_id": "B"})  # item-level jobs may overlap


@pytest.mark.parametrize(
    "job_type,params,exc",
    [
        (JobType.SINGLE, {}, DomainError),
        (JobType.SINGLE, {"external_ids": ["a", "b"]}, DomainError),
        (JobType.BULK, {"external_ids": [str(i) for i in range(1001)]}, DomainError),
        (JobType.CATEGORY, {"category": "shoes"}, BusinessRuleViolation),  # Trendyol has no category filter
        (JobType.INCREMENTAL, {}, DomainError),  # no earlier successful job to derive 'since' from
        (JobType.REPAIR, {}, DomainError),
    ],
)
def test_job_parameters_are_validated(src, job_type, params, exc):
    with pytest.raises(exc):
        start(src, job_type, params=params)


def test_single_and_bulk_imports_by_identifier(src):
    factory, _ = provider_for(products(3))
    with use_provider("trendyol", factory):
        single = run_job_sync(start(src, JobType.SINGLE, params={"external_id": "P1"}))
        bulk = run_job_sync(start(src, JobType.BULK, params={"external_ids": ["P1", "P2", "P2", "NOPE"]}))
    assert single.status == JobStatus.SUCCEEDED and single.n_created == 1
    assert bulk.status == JobStatus.PARTIALLY_FAILED and bulk.n_total == 3
    assert (bulk.n_unchanged, bulk.n_created, bulk.n_failed) == (1, 1, 1)
    assert bulk.items.get(external_id="NOPE").error_code == "not_found"


def test_category_import_where_the_source_supports_it():
    ns = f.source("namshi", base_currency="AED")
    ns.integration_type = IntegrationType.MANUAL
    f.axes()
    f.option("size", "40")
    f.option("color", "black")
    from apps.product_sources.providers.namshi import NamshiProvider

    f.brand("Acme Sport")
    job = start(ns, JobType.CATEGORY, params={"category": "Men/Shoes"})
    job = run_job_sync(job)
    assert job.status in (JobStatus.SUCCEEDED, JobStatus.PARTIALLY_FAILED) and job.n_seen == 1
    assert NamshiProvider.capabilities.category_filter


def test_incremental_import_passes_since_to_the_provider(src):
    seen = {}

    class Spy(InMemoryProvider):
        def discover(self, **kw):
            seen.update(kw)
            return super().discover(**kw)

    with use_provider("trendyol", lambda s, **kw: Spy(s, [], **kw)):
        job = run_job_sync(start(src, JobType.INCREMENTAL, params={"since": "2026-01-02T03:04:05+00:00"}))
    assert job.status == JobStatus.SUCCEEDED and seen["since"].year == 2026


def test_sync_refreshes_known_listings_and_delists_vanished_ones(src):
    factory, holder = provider_for(products(2))
    with use_provider("trendyol", factory):
        run_job_sync(start(src))
        p = holder["p"]
        p.products["P1"] = make_product(
            "P1", title="Acme Shoe 1", variants=[make_variant("V1", size="40", price="150")], raw={"v": 2}
        )
        del p.products["P2"]
        job = run_job_sync(start(src, JobType.SYNC))
    assert job.status == JobStatus.SUCCEEDED and (job.n_updated, job.n_unchanged) == (2, 0)
    assert ExternalProduct.objects.get(external_id="P2").status == "delisted"
    ev = ExternalProduct.objects.get(external_id="P1").variants.get()
    assert str(ev.current_price.list_amount) == "150.00" and ev.prices.count() == 2


# ------------------------------------------------------------------ schedules, retention, Celery wrapper
def test_scheduled_imports_are_idempotent_per_window(src, settings):
    settings.IMPORTS_SCHEDULES = {"trendyol": {"job_type": "full", "every_minutes": 60}}
    now = timezone.now()
    first = jobs.run_scheduled(now)
    assert len(first) == 1 and jobs.run_scheduled(now) == []  # same window / still active
    job = ImportJob.objects.get(pk=first[0])
    assert job.trigger == JobTrigger.SCHEDULED
    ImportJob.objects.filter(pk=job.pk).update(status=JobStatus.SUCCEEDED)
    assert jobs.run_scheduled(now) == []  # same window: the idempotency key already exists
    assert len(jobs.run_scheduled(now + timedelta(hours=1))) == 1


def test_scheduling_skips_sources_that_are_not_authorised(src, settings):
    settings.IMPORTS_SCHEDULES = {"trendyol": {"job_type": "full", "every_minutes": 60}}
    settings.IMPORTS_PROVIDER_MODE = "live"
    assert jobs.run_scheduled() == [] and ImportJob.objects.count() == 0


def test_old_logs_are_purged(src, settings):
    job = start(src)
    old = log(job, "info", "old")
    ImportLog.objects.filter(pk=old.pk).update(
        created_at=timezone.now() - timedelta(days=settings.IMPORTS_LOG_RETENTION_DAYS + 1)
    )
    assert jobs.purge_logs() == 1 and job.logs.exists()


def test_celery_task_requeues_until_the_job_is_done(src, monkeypatch):
    queued = []
    monkeypatch.setattr(
        tasks.run_job, "apply_async", lambda args=None, countdown=None, queue=None: queued.append((args, countdown))
    )
    outcomes = iter([jobs.RunOutcome("continue"), jobs.RunOutcome("retry", 42.0), jobs.RunOutcome("done")])
    monkeypatch.setattr(jobs, "run_slice", lambda job_id: next(outcomes))
    assert [tasks.run_job.apply(args=[7]).get() for _ in range(3)] == ["continue", "retry", "done"]
    assert queued == [([7], None), ([7], 42)]


def test_beat_tasks_are_all_registered():
    from config.celery import app

    for name in (
        "imports.run_job",
        "imports.run_scheduled",
        "imports.recover_stale",
        "imports.purge_logs",
        "imports.refresh_readiness",
        "imports.request_translation",
        "imports.request_pricing",
    ):
        assert name in app.tasks
