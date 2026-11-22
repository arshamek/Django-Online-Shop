import uuid

import pytest
from django.contrib.auth.models import Group, Permission

from apps.audit.models import AuditLog
from apps.catalog.models import (
    Product,
    ProductImage,
    ProductTranslation,
    TranslationOrigin,
    TranslationStatus,
    VariantPrice,
)
from apps.imports.models import ImportJob
from apps.product_sources.providers.mock import InMemoryProvider
from apps.product_sources.providers.registry import use_provider
from tests import catalog_factories as f
from tests.conftest import API
from tests.imports_helpers import import_one, make_product, make_variant, run_job_sync, world

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_enqueue")]
BASE = f"{API}/staff/imports"
NOPE = uuid.uuid4()

ENDPOINTS = [
    ("get", f"{BASE}/sources"),
    ("get", f"{BASE}/jobs"),
    ("post", f"{BASE}/jobs"),
    ("get", f"{BASE}/jobs/{NOPE}"),
    ("post", f"{BASE}/jobs/{NOPE}/cancel"),
    ("post", f"{BASE}/jobs/{NOPE}/retry"),
    ("get", f"{BASE}/jobs/{NOPE}/logs"),
    ("get", f"{BASE}/jobs/{NOPE}/items"),
    ("get", f"{BASE}/products"),
    ("get", f"{BASE}/products/{NOPE}"),
    ("post", f"{BASE}/products/{NOPE}/approve"),
    ("post", f"{BASE}/products/{NOPE}/reject"),
    ("post", f"{BASE}/products/{NOPE}/publish"),
]


@pytest.fixture
def manager(make_staff, login):
    return login(make_staff(role="Import Manager"))


def only(permission, make_staff, login):
    group = Group.objects.create(name=f"only-{permission}")
    group.permissions.add(Permission.objects.get(codename=permission))
    return login(make_staff(role=group.name))


@pytest.fixture
def src():
    return world()[0]


def factory_for(products):
    return lambda source, **kw: InMemoryProvider(source, products, **kw)


# ------------------------------------------------------------------ authorisation
@pytest.mark.parametrize("method,url", ENDPOINTS)
def test_anonymous_customers_and_unrelated_staff_are_refused(method, url, client, make_user, make_staff, login):
    assert getattr(client, method)(url).status_code in (401, 403)
    customer = login(make_user())
    assert getattr(customer, method)(url, {}, format="json").status_code == 403
    catalog_only = login(make_staff(role="Product Manager"))
    assert getattr(catalog_only, method)(url, {}, format="json").status_code == 403


def test_each_action_needs_its_own_permission(make_staff, login, src):
    manage_only = only("imports_manage", make_staff, login)
    review_only = only("imports_review_matches", make_staff, login)
    for url in (f"{BASE}/products/{NOPE}/approve", f"{BASE}/products/{NOPE}/reject"):
        assert manage_only.post(url, {}, format="json").status_code == 403
        assert review_only.post(url, {}, format="json").status_code == 404  # allowed in; the id simply does not exist
    for method, url in (("post", f"{BASE}/jobs"), ("get", f"{BASE}/jobs"), ("post", f"{BASE}/products/{NOPE}/publish")):
        assert getattr(review_only, method)(url, {}, format="json").status_code == 403
    assert manage_only.get(f"{BASE}/jobs").status_code == 200


# ------------------------------------------------------------------ jobs
def test_start_import_status_logs_items_and_summary(manager, src):
    with use_provider(
        "trendyol", factory_for([make_product("P1"), make_product("P2", title="Second", variants=[make_variant("V2")])])
    ):
        r = manager.post(f"{BASE}/jobs", {"source": "trendyol", "job_type": "full"}, format="json")
        assert r.status_code == 201 and r.data["status"] == "queued" and r.data["provider_mode"] == "fixture"
        job_id = r.data["id"]
        run_job_sync(ImportJob.objects.get(public_id=job_id))
    detail = manager.get(f"{BASE}/jobs/{job_id}").data
    assert (
        detail["status"] == "succeeded"
        and detail["progress"]["percent"] == 100.0
        and detail["progress"]["created"] == 2
    )
    assert detail["summary"]["created"] == 2
    logs = manager.get(f"{BASE}/jobs/{job_id}/logs").data
    assert logs["results"] and all(x["level"] and x["message"] for x in logs["results"])
    last = logs["results"][-1]["id"]
    assert manager.get(f"{BASE}/jobs/{job_id}/logs", {"after_id": last}).data["results"] == []
    assert manager.get(f"{BASE}/jobs/{job_id}/logs", {"level": "error"}).data["results"] == []
    assert manager.get(f"{BASE}/jobs/{job_id}/logs", {"level": "bogus"}).status_code == 400
    items = manager.get(f"{BASE}/jobs/{job_id}/items", {"status": "succeeded"}).data
    assert {i["external_id"] for i in items["results"]} == {"P1", "P2"}
    listing = manager.get(f"{BASE}/jobs", {"status": "succeeded", "source": "trendyol"}).data
    assert [j["id"] for j in listing["results"]] == [job_id]
    assert AuditLog.objects.filter(action="imports.job.started").count() == 1


def test_idempotency_key_header_makes_start_safe_to_repeat(manager, src):
    body = {"source": "trendyol", "job_type": "full"}
    first = manager.post(f"{BASE}/jobs", body, format="json", HTTP_IDEMPOTENCY_KEY="abc")
    again = manager.post(f"{BASE}/jobs", body, format="json", HTTP_IDEMPOTENCY_KEY="abc")
    assert (first.status_code, again.status_code) == (201, 200) and first.data["id"] == again.data["id"]
    assert ImportJob.objects.count() == 1


def test_start_validation_and_conflicts(manager, src):
    assert manager.post(f"{BASE}/jobs", {"source": "nope", "job_type": "full"}, format="json").status_code == 400
    assert manager.post(f"{BASE}/jobs", {"source": "trendyol", "job_type": "wat"}, format="json").status_code == 400
    r = manager.post(f"{BASE}/jobs", {"source": "trendyol", "job_type": "single"}, format="json")
    assert r.status_code == 400 and r.data["error"]["code"] == "validation_error"
    assert manager.post(f"{BASE}/jobs", {"source": "trendyol", "job_type": "full"}, format="json").status_code == 201
    dup = manager.post(f"{BASE}/jobs", {"source": "trendyol", "job_type": "full"}, format="json")
    assert dup.status_code == 409 and dup.data["error"]["code"] == "import_already_running"
    cat = manager.post(f"{BASE}/jobs", {"source": "trendyol", "job_type": "category", "category": "x"}, format="json")
    assert cat.status_code == 422 and cat.data["error"]["code"] == "unsupported_job_type"


def test_live_mode_without_authorisation_is_a_clear_422(manager, src, settings):
    settings.IMPORTS_PROVIDER_MODE = "live"
    r = manager.post(f"{BASE}/jobs", {"source": "trendyol", "job_type": "full"}, format="json")
    assert r.status_code == 422 and r.data["error"]["code"] == "source_not_authorized"


def test_cancel_and_retry_endpoints(manager, src):
    job_id = manager.post(f"{BASE}/jobs", {"source": "trendyol", "job_type": "full"}, format="json").data["id"]
    assert manager.post(f"{BASE}/jobs/{job_id}/retry").status_code == 409  # not finished
    r = manager.post(f"{BASE}/jobs/{job_id}/cancel")
    assert r.status_code == 202 and r.data["status"] == "cancelled"
    assert manager.post(f"{BASE}/jobs/{job_id}/cancel").data["error"]["code"] == "job_finished"
    r = manager.post(f"{BASE}/jobs/{job_id}/retry")
    assert r.status_code == 202 and r.data["status"] == "queued" and r.data["retry_count"] == 1
    assert {a.action for a in AuditLog.objects.all()} >= {"imports.job.cancel_requested", "imports.job.retried"}


def test_unknown_ids_are_404(manager):
    assert manager.get(f"{BASE}/jobs/{NOPE}").status_code == 404
    assert manager.get(f"{BASE}/products/{NOPE}").status_code == 404


def test_sources_endpoint_is_honest_about_live_access(manager, src):
    f.source("namshi")
    rows = {r["code"]: r for r in manager.get(f"{BASE}/sources").data["results"]}
    assert set(rows) == {"trendyol", "namshi"}
    t = rows["trendyol"]
    assert t["provider_mode"] == "fixture" and t["live_verified"] is False and t["live_authorised"] is False
    assert t["catalog_scope"] == "own_seller_catalog" and t["capabilities"]["category_import"] is False
    assert any("integration_type" in b for b in t["live_blockers"])
    assert rows["namshi"]["catalog_scope"] == "licensed_feed"


# ------------------------------------------------------------------ products: preview, approve, reject, publish
def held_listing(src):
    return import_one(src, make_product(brand="Mystery Co")).ext


def test_product_list_filters_and_preview(manager, src):
    held = held_listing(src)
    ok = import_one(src, make_product("P2", title="Second", variants=[make_variant("V2")])).ext
    rows = manager.get(f"{BASE}/products", {"pipeline_status": "PENDING_REVIEW"}).data["results"]
    assert [r["id"] for r in rows] == [str(held.public_id)]
    assert manager.get(f"{BASE}/products", {"pipeline_status": "NOPE"}).status_code == 400
    assert manager.get(f"{BASE}/products", {"q": "Second"}).data["results"][0]["id"] == str(ok.public_id)
    preview = manager.get(f"{BASE}/products/{held.public_id}").data
    assert preview["pipeline_status"] == "PENDING_REVIEW" and preview["resolution"]["brand"] is None
    assert preview["variants"][0]["price"]["list"] == "100.00" and preview["images"][0]["url"].startswith("https://")
    assert preview["data_origin"] == "fixture" and "raw_payload" not in preview
    assert "not_matched_to_a_canonical_product" in preview["publication_blockers"]
    ready = manager.get(f"{BASE}/products/{ok.public_id}").data
    assert any(b.startswith("translation_missing") for b in ready["publication_blockers"])


def test_approve_creates_the_product_and_remembers_the_brand(manager, src):
    held = held_listing(src)
    r = manager.post(f"{BASE}/products/{held.public_id}/approve", {"create_new": True, "brand": "acme"}, format="json")
    assert r.status_code == 200 and r.data["match"]["status"] == "manually_matched"
    assert r.data["match"]["product"]["status"] == "draft" and r.data["pipeline_status"] != "PENDING_REVIEW"
    again = manager.post(f"{BASE}/products/{held.public_id}/approve", {"create_new": True}, format="json")
    assert again.status_code == 409 and again.data["error"]["code"] == "not_pending_review"
    assert AuditLog.objects.filter(action="imports.product.approved").exists()


def test_approve_can_link_an_existing_product_and_validates_input(manager, src):
    first = import_one(src, make_product("A", title="Acme Court Shoe")).ext
    other = f.source("namshi", base_currency="AED")
    held = import_one(other, make_product("B", brand="Mystery Co", title="Zzz")).ext
    product_id = str(first.canonical_product.public_id)
    bad = manager.post(f"{BASE}/products/{held.public_id}/approve", {"product": str(uuid.uuid4())}, format="json")
    assert bad.status_code == 400
    both = manager.post(
        f"{BASE}/products/{held.public_id}/approve", {"product": product_id, "create_new": True}, format="json"
    )
    assert both.status_code == 409 and both.data["error"]["code"] == "ambiguous_decision"
    bad_opt = manager.post(
        f"{BASE}/products/{held.public_id}/approve",
        {"product": product_id, "option_mappings": [{"attribute": "size", "raw": "x", "option": "nope"}]},
        format="json",
    )
    assert bad_opt.status_code == 400
    ok = manager.post(
        f"{BASE}/products/{held.public_id}/approve", {"product": product_id, "brand": "acme"}, format="json"
    )
    assert ok.status_code == 200 and ok.data["match"]["product"]["id"] == product_id
    assert Product.objects.count() == 1


def test_reject_archives_the_listing(manager, src):
    held = held_listing(src)
    r = manager.post(f"{BASE}/products/{held.public_id}/reject", {"reason": "off-range"}, format="json")
    assert r.status_code == 200 and r.data["pipeline_status"] == "ARCHIVED" and r.data["match"]["status"] == "rejected"
    assert AuditLog.objects.filter(action="imports.product.rejected").exists()


def test_publish_endpoint_applies_the_business_rules(manager, src):
    ext = import_one(src, make_product()).ext
    blocked = manager.post(f"{BASE}/products/{ext.public_id}/publish")
    assert blocked.status_code == 422 and blocked.data["error"]["code"] == "not_ready_to_publish"
    product = ext.canonical_product
    ProductTranslation.objects.create(
        product=product, language="fa", name="کفش", origin=TranslationOrigin.HUMAN, status=TranslationStatus.HUMAN
    )
    for v in product.variants.all():
        VariantPrice.objects.create(
            variant=v, original_amount=100, original_currency="TRY", list_irr=1_000_000, final_irr=900_000
        )
    from apps.imports.services.readiness import refresh_readiness

    refresh_readiness(ext)
    no_image = manager.post(f"{BASE}/products/{ext.public_id}/publish")
    assert no_image.status_code == 422 and no_image.data["error"]["code"] == "not_publishable"
    assert "image_missing" in no_image.data["error"]["fields"]["blockers"]
    ProductImage.objects.create(product=product, image="products/x.jpg", rights="owned")
    done = manager.post(f"{BASE}/products/{ext.public_id}/publish")
    assert done.status_code == 200 and done.data["pipeline_status"] == "PUBLISHED"
    product.refresh_from_db()
    assert product.status == "active" and AuditLog.objects.filter(action="imports.product.published").exists()
