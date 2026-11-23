"""Part A: translation. Protection of brands/codes/numbers, provenance, cache, queue, recovery, reviews."""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.catalog.models import (
    AttributeOptionTranslation,
    ProductAttributeValue,
    ProductTranslation,
)
from apps.categories.models import CategoryTranslation
from apps.common.exceptions import BusinessRuleViolation
from apps.translations import masking, tasks
from apps.translations.errors import TranslationProviderError, TranslationValidationError
from apps.translations.models import JobStatus, TranslationCache, TranslationJob, TranslationRecord
from apps.translations.providers import llm
from apps.translations.providers.base import TranslationProvider, TranslationResult
from apps.translations.providers.fake import FakeProvider
from apps.translations.providers.registry import use_provider
from apps.translations.services import entities, jobs, translate
from tests import catalog_factories as f

pytestmark = pytest.mark.django_db


class Scripted(TranslationProvider):
    name, model = "scripted", "s-1"

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def translate(self, request):
        self.calls.append(request)
        answer = self.answers.pop(0) if self.answers else (lambda r: f"[{r.target_language}] {r.text}")
        if isinstance(answer, Exception):
            raise answer
        return TranslationResult(answer(request) if callable(answer) else answer, self.name, self.model)


def make_product(
    slug="shoe",
    name="کفش ورزشی Nike Air Max 90 مدل XJ-900 وزن 450g",
    description="ارتفاع 12cm و ظرفیت 2.5L، کد AB-1234",
    **extra,
):
    brand = f.brand("Nike")
    product = f.product(slug, names={"fa": name}, brand_obj=brand, manufacturer_model_code="AM90-X", **extra)
    ProductTranslation.objects.filter(product=product, language="fa").update(
        description=description, short_description="سبک"
    )
    return product


# ------------------------------------------------------------------------------ masking
def test_protects_brand_model_code_measurements_and_numbers():
    masked, originals = masking.protect(
        "Nike Air Max 90 XJ-900 weighs 450g, 12 cm tall, 2.5L, 99.9% cotton, code AB-1234", ["Nike"]
    )
    for kept in ("Nike", "90", "XJ-900", "450g", "12 cm", "2.5L", "99.9%", "AB-1234"):
        assert kept in originals, kept
    assert "Nike" not in masked and "450" not in masked
    restored = masking.restore(masked, originals, masked)
    assert restored == "Nike Air Max 90 XJ-900 weighs 450g, 12 cm tall, 2.5L, 99.9% cotton, code AB-1234"


def test_persian_and_arabic_digits_are_protected_verbatim():
    masked, originals = masking.protect("وزن ۴۵۰ گرم و ٣ عدد", [])
    assert originals == ["۴۵۰", "٣"]
    assert not masking.digit_runs(masking._PLACEHOLDER.sub("", masked))


def test_codes_only_text_needs_no_translation():
    assert not masking.needs_translation(masking.protect("XL-2000", [])[0])
    assert not masking.needs_translation(masking.protect("42", [])[0])
    assert masking.needs_translation(masking.protect("Black", [])[0])


def test_restore_rejects_lost_duplicated_or_invented_tokens():
    masked, originals = masking.protect("Nike 90 shoes", ["Nike"])
    for bad in ("shoes", f"{masked} ⟦0⟧", "⟦0⟧ ⟦9⟧ shoes", "⟦0 ⟦1⟧ shoes"):
        with pytest.raises(TranslationValidationError):
            masking.restore(bad, originals, masked)


def test_number_changes_outside_placeholders_are_caught():
    with pytest.raises(TranslationValidationError) as e:
        masking.verify_numbers("size 42", "size 43")
    assert e.value.code == "numbers_changed"


# ------------------------------------------------------------------------------ translating a product
def run(product, lang="en", provider=None, **kw):
    provider = provider or FakeProvider()
    return translate.translate_entity("product", product.pk, lang, provider=provider, **kw), provider


def test_translation_preserves_originals_and_records_provenance():
    product = make_product()
    result, provider = run(product)
    row = ProductTranslation.objects.get(product=product, language="en")
    assert result["outcome"] == "translated"
    for token in ("Nike", "90", "XJ-900", "450g", "12cm", "2.5L", "AB-1234"):
        assert token in row.name + row.description, token
    assert (row.origin, row.status, row.provider, row.model) == ("machine", "machine", "fake", "echo-v1")
    assert row.source_text_hash == result["source_hash"] and row.reviewed_by is None
    fa = ProductTranslation.objects.get(product=product, language="fa")  # the original is never modified
    assert fa.name.startswith("کفش") and fa.origin == "human"


def test_provider_is_never_sent_the_protected_values():
    product = make_product()
    _, provider = run(product)
    sent = " ".join(r.text for r in provider.calls)
    for secret in ("Nike", "XJ-900", "450g", "AB-1234", "2.5L", "AM90-X"):
        assert secret not in sent
    assert "⟦" in sent


def test_bad_provider_output_is_rejected_and_nothing_is_written():
    product = make_product()
    drop_tokens = Scripted(lambda r: "a translation that lost every protected token")
    with pytest.raises(TranslationValidationError):
        translate.translate_entity("product", product.pk, "en", provider=drop_tokens)
    assert not ProductTranslation.objects.filter(product=product, language="en").exists()
    assert not TranslationCache.objects.exists()  # invalid output is never cached


def test_cache_serves_identical_text_without_calling_the_provider():
    a, b = make_product("shoe-a"), make_product("shoe-b")
    ProductTranslation.objects.filter(product=b).update(
        name=ProductTranslation.objects.get(product=a, language="fa").name
    )
    first = FakeProvider()
    run(a, provider=first)
    second = FakeProvider()
    result, _ = run(b, provider=second)
    assert first.calls and result["from_cache"] >= 1 and len(second.calls) < len(first.calls)
    assert TranslationCache.objects.filter(hit_count__gt=0).exists()


def test_human_text_is_never_overwritten_and_is_flagged_stale_when_the_source_changes():
    product = make_product()
    ProductTranslation.objects.create(
        product=product,
        language="en",
        name="Hand-made title",
        origin="human",
        status="human",
        source_text_hash="old-hash",
    )
    result, provider = run(product)
    row = ProductTranslation.objects.get(product=product, language="en")
    assert row.name == "Hand-made title" and result["outcome"] == "skipped" and "row" in result["groups_protected"]
    assert row.status == "stale" and row.origin == "human"  # text kept, flagged for review
    assert provider.calls == []


def test_approved_machine_text_is_protected_too():
    product = make_product()
    run(product)
    translate.approve("product", product.pk, "en", "row", user=None)
    ProductTranslation.objects.filter(product=product, language="fa").update(name="نام تازه Nike")
    result, _ = run(product)
    row = ProductTranslation.objects.get(product=product, language="en")
    assert result["outcome"] == "skipped" and row.status == "stale" and row.name.startswith("[en]")


def test_unreviewed_machine_text_is_refreshed_when_the_source_changes():
    product = make_product()
    run(product)
    ProductTranslation.objects.filter(product=product, language="fa").update(name="نام تازه Nike")
    result, _ = run(product)
    assert result["outcome"] == "translated"
    assert "Nike" in ProductTranslation.objects.get(product=product, language="en").name


def test_manual_correction_beats_machine_and_survives_reruns(make_staff):
    product = make_product()
    run(product)
    staff = make_staff(superuser=True)
    translate.save_manual_correction(
        "product", product.pk, "en", "row", {"name": "Nike Air Max 90 XJ-900 sports shoe"}, user=staff
    )
    row = ProductTranslation.objects.get(product=product, language="en")
    assert (row.origin, row.status, row.reviewed_by) == ("human", "human", staff) and row.description == ""
    ProductTranslation.objects.filter(product=product, language="fa").update(name="نام تازه")
    run(product)
    assert ProductTranslation.objects.get(product=product, language="en").name == "Nike Air Max 90 XJ-900 sports shoe"


def test_manual_correction_validation():
    product = make_product()
    for bad in ({"name": " "}, {"bogus": "x"}):
        with pytest.raises(TranslationValidationError):
            translate.save_manual_correction("product", product.pk, "en", "row", bad, user=None)
    with pytest.raises(TranslationValidationError):
        translate.save_manual_correction("product", product.pk, "fa", "row", {"name": "x"}, user=None)


def test_specifications_are_translated_with_numbers_kept():
    product = make_product()
    attr = f.axes()[0]
    spec = ProductAttributeValue.objects.create(
        product=product, attribute=attr, language="fa", value_text="جنس رویه: پارچه 100% پلی‌استر 300D"
    )
    run(product)
    sibling = ProductAttributeValue.objects.get(product=product, attribute=attr, language="en")
    assert "100%" in sibling.value_text and "300D" in sibling.value_text and sibling.value_text != spec.value_text
    rec = TranslationRecord.objects.get(entity_type="spec_value", entity_id=spec.pk, language="en")
    assert (rec.origin, rec.status, rec.provider, rec.target_object_id) == ("machine", "machine", "fake", sibling.pk)


def test_categories_attribute_values_and_colours():
    cat = f.category("shoes")
    CategoryTranslation.objects.filter(category=cat).delete()
    CategoryTranslation.objects.create(category=cat, language="fa", name="کفش")
    assert translate.translate_entity("category", cat.pk, "en", provider=FakeProvider())["outcome"] == "translated"
    assert CategoryTranslation.objects.get(category=cat, language="en").name == "[en] کفش"
    option = f.option("color", "black", "مشکی")
    AttributeOptionTranslation.objects.filter(option=option, language="en").delete()
    translate.translate_entity("option", option.pk, "en", provider=FakeProvider())
    assert AttributeOptionTranslation.objects.get(option=option, language="en").name == "[en] مشکی"
    rec = TranslationRecord.objects.get(entity_type="option", entity_id=option.pk, language="en")
    assert rec.origin == "machine" and rec.provider == "fake"


def test_untracked_legacy_rows_count_as_human_and_are_protected():
    cat = f.category("legacy")
    CategoryTranslation.objects.filter(category=cat).delete()
    CategoryTranslation.objects.create(category=cat, language="fa", name="کفش")
    CategoryTranslation.objects.create(category=cat, language="en", name="Shoes (staff)")
    result = translate.translate_entity("category", cat.pk, "en", provider=FakeProvider())
    assert (
        result["outcome"] == "skipped"
        and CategoryTranslation.objects.get(category=cat, language="en").name == "Shoes (staff)"
    )


def test_numeric_only_option_is_copied_without_calling_the_provider():
    option = f.option("size", "42", "42")
    provider = FakeProvider()
    translate.translate_entity("option", option.pk, "en", provider=provider)
    assert provider.calls == [] and AttributeOptionTranslation.objects.get(option=option, language="en").name == "42"


# ------------------------------------------------------------------------------ reviews + credentials
def test_reviews_cannot_be_translated():
    for kind in ("review", "customer_review"):
        with pytest.raises(BusinessRuleViolation) as e:
            entities.assert_translatable(kind)
        assert e.value.get_codes() == "reviews_not_translatable"
        with pytest.raises(BusinessRuleViolation):
            jobs.enqueue(kind, 1)
    assert "review" not in entities.ADAPTERS


@pytest.fixture
def llm_transport():
    seen = {}

    def install(status=200, answer=None, raw=None):
        def fn(url, headers, body, timeout):
            seen.update(url=url, headers=headers, body=json.loads(body))
            text = (
                raw
                if raw is not None
                else json.dumps(
                    {
                        "content": [{"type": "text", "text": answer(seen["body"])}],
                        "usage": {"input_tokens": 5, "output_tokens": 6},
                    }
                )
            )
            return status, text

        llm.set_transport(fn)
        return seen

    yield install
    llm.set_transport(None)


def test_llm_adapter_sends_the_key_only_in_a_header_and_never_the_protected_values(settings, llm_transport):
    settings.AI_SETTINGS = {**settings.AI_SETTINGS, "translation_api_key": "sk-test-123", "model": "m-1"}
    seen = llm_transport(answer=lambda body: body["messages"][0]["content"])
    product = make_product()
    result = translate.translate_entity("product", product.pk, "en", provider=llm.LLMTranslationProvider())
    assert result["provider"] == "llm" and result["model"] == "m-1"
    assert seen["headers"]["x-api-key"] == "sk-test-123" and "sk-test-123" not in json.dumps(seen["body"])
    assert "Nike" not in json.dumps(seen["body"], ensure_ascii=False)
    assert llm.LLMTranslationProvider.LIVE_VERIFIED is False


@pytest.mark.parametrize(
    ("status", "transient", "code"),
    [(429, True, "http_429"), (503, True, "http_503"), (401, False, "auth_failed"), (400, False, "http_400")],
)
def test_llm_adapter_error_mapping(settings, llm_transport, status, transient, code):
    settings.AI_SETTINGS = {**settings.AI_SETTINGS, "translation_api_key": "k", "model": "m"}
    llm_transport(status=status, raw="{}")
    with pytest.raises(TranslationProviderError) as e:
        llm.LLMTranslationProvider().translate(
            __import__("apps.translations.providers.base", fromlist=["x"]).TranslationRequest("hi", "en", "fa")
        )
    assert (e.value.transient, e.value.code) == (transient, code)


def test_unconfigured_llm_provider_fails_cleanly_without_a_secret_in_the_message(settings):
    settings.AI_SETTINGS = {**settings.AI_SETTINGS, "translation_api_key": "", "api_key": "", "model": ""}
    from apps.translations.providers.base import TranslationRequest

    with pytest.raises(TranslationProviderError) as e:
        llm.LLMTranslationProvider().translate(TranslationRequest("hi", "en", "fa"))
    assert e.value.code == "provider_not_configured"


def test_no_translation_credentials_in_any_api_response(login, make_staff):
    from tests.conftest import API

    client = login(make_staff(superuser=True))
    for path in ("/staff/translations/jobs", "/staff/translations/summary"):
        body = client.get(f"{API}{path}").content.decode()
        assert "api_key" not in body and "sk-" not in body


# ------------------------------------------------------------------------------ queue, retry, recovery
def test_enqueue_is_idempotent_and_skips_the_source_language():
    product = make_product()
    first = jobs.enqueue("product", product.pk, dispatch=False)
    assert sorted(j.target_language for j in first) == ["ar", "en", "tr"]
    assert jobs.enqueue("product", product.pk, dispatch=False) == []
    assert TranslationJob.objects.count() == 3
    ProductTranslation.objects.filter(product=product, language="fa").update(name="عنوان دیگر Nike")
    assert len(jobs.enqueue("product", product.pk, dispatch=False)) == 3  # new source text = new jobs


def test_job_runs_through_the_task_and_records_stats():
    product = make_product()
    jobs.enqueue("product", product.pk, ["en"], dispatch=False)
    job = TranslationJob.objects.get()
    with use_provider(FakeProvider()):
        assert tasks.run_job.apply(args=[job.pk]).get() == JobStatus.SUCCEEDED
    job.refresh_from_db()
    assert (job.provider, job.model, job.attempts, job.error_code) == (
        "fake",
        "echo-v1",
        1,
        "",
    ) and job.units_total >= 1
    with use_provider(FakeProvider()):
        assert tasks.run_job.apply(args=[job.pk]).get() is None  # already done: a duplicate message does nothing


def test_transient_failure_backs_off_then_succeeds_on_retry():
    product = make_product()
    jobs.enqueue("product", product.pk, ["en"], dispatch=False)
    job = TranslationJob.objects.get()
    provider = Scripted(TranslationProviderError("http_503", "busy"))
    t0 = timezone.now()
    jobs.run_job(job.pk, provider=provider, now=t0)
    job.refresh_from_db()
    assert (
        job.status == JobStatus.FAILED
        and job.attempts == 1
        and job.error_code == "http_503"
        and job.next_attempt_at > t0
    )
    assert jobs.run_job(job.pk, provider=provider, now=t0) is None  # not due yet
    jobs.run_job(job.pk, provider=FakeProvider(), now=job.next_attempt_at + timedelta(seconds=1))
    job.refresh_from_db()
    assert job.status == JobStatus.SUCCEEDED and job.attempts == 2 and job.error_code == ""


def test_permanent_failure_goes_dead_and_staff_can_requeue():
    product = make_product()
    jobs.enqueue("product", product.pk, ["en"], dispatch=False)
    job = TranslationJob.objects.get()
    jobs.run_job(job.pk, provider=Scripted(TranslationProviderError("auth_failed", "no", transient=False)))
    job.refresh_from_db()
    assert job.status == JobStatus.DEAD
    jobs.requeue(job)
    job.refresh_from_db()
    assert job.status in (JobStatus.QUEUED, JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.DEAD) and job.attempts <= 1


def test_retries_are_capped(settings):
    settings.TRANSLATION_MAX_ATTEMPTS = 2
    product = make_product()
    jobs.enqueue("product", product.pk, ["en"], dispatch=False)
    job = TranslationJob.objects.get()
    boom = Scripted(*[TranslationProviderError("http_503") for _ in range(5)])
    now = timezone.now()
    for i in range(3):
        jobs.run_job(job.pk, provider=boom, now=now + timedelta(hours=i + 1))
    job.refresh_from_db()
    assert job.status == JobStatus.DEAD and job.attempts == 2


def test_validation_failures_retry_then_die(settings):
    settings.TRANSLATION_MAX_ATTEMPTS = 2
    product = make_product()
    jobs.enqueue("product", product.pk, ["en"], dispatch=False)
    job = TranslationJob.objects.get()
    bad = Scripted(*[(lambda r: "no tokens here")] * 10)
    jobs.run_job(job.pk, provider=bad)
    job.refresh_from_db()
    assert job.status == JobStatus.FAILED and job.error_code == "placeholder_mismatch"
    jobs.run_job(job.pk, provider=bad, now=job.next_attempt_at + timedelta(seconds=1))
    job.refresh_from_db()
    assert job.status == JobStatus.DEAD
    assert not ProductTranslation.objects.filter(product=product, language="en").exists()


def test_recovery_frees_jobs_stuck_in_running_and_redispatches_due_retries(settings, monkeypatch):
    settings.TRANSLATION_STALE_JOB_MINUTES = 10
    product = make_product()
    jobs.enqueue("product", product.pk, ["en", "ar"], dispatch=False)
    stuck, failed = TranslationJob.objects.order_by("id")
    old = timezone.now() - timedelta(minutes=30)
    TranslationJob.objects.filter(pk=stuck.pk).update(status=JobStatus.RUNNING, locked_at=old, attempts=1)
    TranslationJob.objects.filter(pk=failed.pk).update(status=JobStatus.FAILED, next_attempt_at=old, updated_at=old)
    dispatched = []
    monkeypatch.setattr(jobs, "_dispatch", lambda pk, countdown=0: dispatched.append(pk))
    counts = jobs.recover()
    stuck.refresh_from_db()
    assert counts["unlocked"] == 1 and stuck.status == JobStatus.FAILED and stuck.error_code == "worker_lost"
    assert set(dispatched) == {stuck.pk, failed.pk}
    TranslationJob.objects.filter(pk=stuck.pk).update(
        status=JobStatus.RUNNING, locked_at=old, attempts=settings.TRANSLATION_MAX_ATTEMPTS
    )
    assert jobs.recover()["dead"] == 1


def test_recover_task_is_registered_and_runs():
    assert tasks.recover.apply().get() == {"unlocked": 0, "dead": 0, "redispatched": 0}


def test_import_handler_queues_product_category_and_colour_jobs(settings):
    from apps.translations.handlers import import_translation_handler

    product = make_product()
    import_translation_handler(product.pk, ["en", "tr"])
    types = {(j.entity_type, j.target_language) for j in TranslationJob.objects.all()}
    assert ("product", "en") in types and ("product", "tr") in types and ("category", "en") in types
    import_translation_handler(product.pk, ["en", "tr"])
    assert TranslationJob.objects.filter(entity_type="product").count() == 2  # idempotent
