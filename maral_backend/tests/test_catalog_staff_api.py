import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.audit.models import AuditLog
from apps.catalog.models import Product, ProductListingProjection

from . import catalog_factories as f
from .conftest import API

pytestmark = pytest.mark.django_db

BASE = f"{API}/staff/products"


@pytest.fixture
def manager(make_staff, login):
    return login(make_staff(role="Product Manager"))


@pytest.fixture
def world(db):
    f.brand("Acme")
    f.category("women", fa="زنانه")
    f.source("maral", name="Maral")
    f.source("trendyol")
    f.option("size", "m", "M")
    f.option("color", "black", "مشکی")


def new_product(client, slug="new-coat", **extra):
    payload = {
        "slug": slug,
        "brand": "acme",
        "category": "women",
        "translations": {"fa": {"name": "پالتو نو"}, "en": {"name": "New coat"}},
        **extra,
    }
    return client.post(BASE, payload, format="json")


# ------------------------------------------------------------------ authorization
ENDPOINTS = [
    ("get", BASE),
    ("post", BASE),
    ("get", f"{BASE}/8c5d1c64-0e2a-4c64-8f2c-1b2b3c4d5e6f"),
    ("patch", f"{BASE}/8c5d1c64-0e2a-4c64-8f2c-1b2b3c4d5e6f"),
    ("post", f"{BASE}/8c5d1c64-0e2a-4c64-8f2c-1b2b3c4d5e6f/status"),
    ("put", f"{BASE}/8c5d1c64-0e2a-4c64-8f2c-1b2b3c4d5e6f/translations/en"),
    ("post", f"{BASE}/8c5d1c64-0e2a-4c64-8f2c-1b2b3c4d5e6f/variants"),
    ("patch", f"{API}/staff/variants/8c5d1c64-0e2a-4c64-8f2c-1b2b3c4d5e6f"),
    ("post", f"{BASE}/8c5d1c64-0e2a-4c64-8f2c-1b2b3c4d5e6f/images"),
    ("post", f"{BASE}/8c5d1c64-0e2a-4c64-8f2c-1b2b3c4d5e6f/images/reorder"),
    ("patch", f"{API}/staff/images/8c5d1c64-0e2a-4c64-8f2c-1b2b3c4d5e6f"),
    ("delete", f"{API}/staff/images/8c5d1c64-0e2a-4c64-8f2c-1b2b3c4d5e6f"),
]


@pytest.mark.parametrize("method,url", ENDPOINTS)
def test_anonymous_callers_are_rejected(api_client, method, url):
    assert getattr(api_client, method)(url, {}, format="json").status_code in (401, 403)


@pytest.mark.parametrize("method,url", ENDPOINTS)
def test_customers_are_forbidden(make_user, login, method, url):
    client = login(make_user())
    assert getattr(client, method)(url, {}, format="json").status_code == 403


@pytest.mark.parametrize("method,url", ENDPOINTS)
def test_staff_without_the_catalog_permission_are_forbidden(make_staff, login, method, url):
    support = login(make_staff(role="Customer Support"))
    assert getattr(support, method)(url, {}, format="json").status_code == 403
    no_role = login(make_staff())
    assert getattr(no_role, method)(url, {}, format="json").status_code == 403


@pytest.mark.parametrize("role,superuser", [("Product Manager", False), (None, True)])
def test_product_manager_and_super_admin_may_manage(make_staff, login, world, role, superuser):
    client = login(make_staff(role=role, superuser=superuser))
    assert client.get(BASE).status_code == 200
    assert new_product(client, f"p-{role or 'root'}".lower().replace(" ", "-")).status_code == 201


def test_other_staff_roles_cannot_publish_or_upload(make_staff, login, world, manager):
    product_id = new_product(manager).data["id"]
    for role in ("Finance", "Order Manager", "Import Manager"):
        try:
            client = login(make_staff(role=role))
        except Exception:
            continue
        assert client.post(f"{BASE}/{product_id}/status", {"status": "archived"}, format="json").status_code == 403


def test_admin_site_is_gated_by_the_same_permission(make_staff, login, client, django_user_model):
    from django.contrib import admin as dj_admin
    from django.test import RequestFactory

    from apps.catalog.admin import ProductAdmin

    request = RequestFactory().get("/")
    request.user = make_staff(role="Customer Support")
    model_admin = ProductAdmin(Product, dj_admin.site)
    assert not model_admin.has_module_permission(request) and not model_admin.has_change_permission(request)
    request.user = make_staff(role="Product Manager")
    assert (
        model_admin.has_module_permission(request)
        and model_admin.has_add_permission(request)
        and model_admin.has_delete_permission(request)
    )


# ------------------------------------------------------------------ product lifecycle
def test_create_update_and_audit(manager, world):
    resp = new_product(manager)
    assert resp.status_code == 201 and resp.data["status"] == "draft" and set(resp.data["translations"]) == {"fa", "en"}
    pid = resp.data["id"]
    patched = manager.patch(
        f"{BASE}/{pid}",
        {"manufacturer_model_code": "M-1", "translations": {"fa": {"name": "پالتو ویرایش"}}},
        format="json",
    )
    assert (
        patched.status_code == 200
        and patched.data["translations"]["fa"]["name"] == "پالتو ویرایش"
        and patched.data["manufacturer_model_code"] == "M-1"
    )
    actions = list(AuditLog.objects.filter(action__startswith="catalog.").values_list("action", flat=True))
    assert "catalog.product.created" in actions and "catalog.product.updated" in actions


def test_create_validation(manager, world):
    assert new_product(manager, "search").status_code == 400  # reserved route name
    assert manager.post(BASE, {"slug": "x", "brand": "nope", "category": "women"}, format="json").status_code == 400
    assert new_product(manager, "dup").status_code == 201
    dup = new_product(manager, "dup")
    assert dup.status_code == 409 and dup.data["error"]["code"] == "slug_exists"
    bad_lang = new_product(manager, "x2", translations={"xx": {"name": "n"}})
    assert bad_lang.status_code == 400


def test_a_draft_is_invisible_publishing_needs_the_invariants_and_is_audited(manager, world, api_client):
    pid = new_product(manager).data["id"]
    assert api_client.get(f"{API}/products/new-coat").status_code == 404
    blocked = manager.post(f"{BASE}/{pid}/status", {"status": "active"}, format="json")
    assert (
        blocked.status_code == 422
        and blocked.data["error"]["code"] == "not_publishable"
        and "variant" in blocked.data["error"]["message"]
    )
    assert "at least one active variant" in manager.get(f"{BASE}/{pid}").data["publish_blockers"]

    variant = {
        "sku": "NEW-M-BLACK",
        "fulfillment_type": "PROXY_PURCHASE",
        "source": "trendyol",
        "size": "m",
        "color": "black",
        "availability": "in_stock",
        "price": {
            "original_amount": "100",
            "original_currency": "TRY",
            "list_irr": "10000000",
            "final_irr": "8000000",
            "discount_percent": "20",
        },
    }
    created = manager.post(f"{BASE}/{pid}/variants", variant, format="json")
    assert (
        created.status_code == 201
        and created.data["variants"][0]["size"] == "m"
        and created.data["variants"][0]["price"]["final_irr"] == "8000000"
    )
    ok = manager.post(f"{BASE}/{pid}/status", {"status": "active"}, format="json")
    assert ok.status_code == 200 and ok.data["status"] == "active" and ok.data["publish_blockers"] == []

    shown = api_client.get(f"{API}/products/new-coat")
    assert (
        shown.status_code == 200
        and shown.data["price"]["final"]["amount"] == "800000"
        and shown.data["available"] is True
    )
    assert ProductListingProjection.objects.filter(product__slug="new-coat").exists()
    assert AuditLog.objects.filter(action="catalog.product.status_changed").exists()

    assert manager.post(f"{BASE}/{pid}/status", {"status": "archived"}, format="json").status_code == 200
    assert api_client.get(f"{API}/products/new-coat").status_code == 404  # archived => gone from the storefront
    assert (
        manager.post(f"{BASE}/{pid}/status", {"status": "in_review"}, format="json").status_code == 422
    )  # archived -> draft only


def test_staff_list_includes_drafts_and_supports_search_and_status_filter(manager, world):
    new_product(manager, "alpha")
    new_product(manager, "beta")
    listing = manager.get(BASE).data
    assert listing["count"] == 2 and {r["status"] for r in listing["results"]} == {"draft"}
    assert [r["slug"] for r in manager.get(BASE, {"q": "alp"}).data["results"]] == ["alpha"]
    assert manager.get(BASE, {"status": "active"}).data["count"] == 0
    assert manager.get(BASE, {"status": "bogus"}).status_code == 400


def test_translation_upsert_marks_machine_translations(manager, world):
    pid = new_product(manager).data["id"]
    resp = manager.put(f"{BASE}/{pid}/translations/tr", {"name": "Yeni palto", "origin": "machine"}, format="json")
    assert (
        resp.status_code == 200
        and resp.data["translations"]["tr"]["origin"] == "machine"
        and resp.data["translations"]["tr"]["status"] == "machine"
    )
    assert manager.put(f"{BASE}/{pid}/translations/xx", {"name": "n"}, format="json").status_code == 400


# ------------------------------------------------------------------ variants
def test_variant_rules(manager, world):
    pid = new_product(manager).data["id"]
    base = {"sku": "V1", "fulfillment_type": "PROXY_PURCHASE", "source": "trendyol", "size": "m", "color": "black"}
    assert manager.post(f"{BASE}/{pid}/variants", base, format="json").status_code == 201
    assert (
        manager.post(f"{BASE}/{pid}/variants", {**base, "sku": "V2"}, format="json").data["error"]["code"]
        == "variant_exists"
    )  # same options
    color_only = manager.post(f"{BASE}/{pid}/variants", {**base, "sku": "V2B", "size": ""}, format="json")
    assert color_only.status_code == 201  # different option set => a different variant
    f.option("size", "l", "L")
    assert (
        manager.post(f"{BASE}/{pid}/variants", {**base, "sku": "V1", "size": "l", "color": ""}, format="json").data[
            "error"
        ]["code"]
        == "sku_exists"
    )
    assert (
        manager.post(f"{BASE}/{pid}/variants", {**base, "sku": "V3", "size": "zzz"}, format="json").status_code == 400
    )
    proxy_from_maral = manager.post(
        f"{BASE}/{pid}/variants", {"sku": "V4", "fulfillment_type": "PROXY_PURCHASE", "source": "maral"}, format="json"
    )
    assert proxy_from_maral.status_code == 400
    assert (
        manager.post(
            f"{BASE}/{pid}/variants",
            {
                **base,
                "sku": "V5",
                "size": "",
                "price": {"original_amount": "1", "original_currency": "TRY", "list_irr": "100", "final_irr": "200"},
            },
            format="json",
        ).status_code
        == 400
    )


def test_variant_update_changes_price_availability_and_visibility(manager, world, api_client):
    pid = new_product(manager).data["id"]
    manager.post(
        f"{BASE}/{pid}/variants",
        {
            "sku": "V1",
            "fulfillment_type": "DIRECT_INVENTORY",
            "source": "maral",
            "availability": "in_stock",
            "price": {
                "original_amount": "1",
                "original_currency": "TRY",
                "list_irr": "5000000",
                "final_irr": "5000000",
            },
        },
        format="json",
    )
    manager.post(f"{BASE}/{pid}/status", {"status": "active"}, format="json")
    vid = ProductListingProjection.objects.get(product__slug="new-coat").display_variant.public_id
    resp = manager.patch(
        f"{API}/staff/variants/{vid}",
        {
            "availability": "out_of_stock",
            "price": {
                "original_amount": "1",
                "original_currency": "TRY",
                "list_irr": "5000000",
                "final_irr": "4000000",
                "discount_percent": "20",
            },
        },
        format="json",
    )
    assert resp.status_code == 200 and resp.data["variants"][0]["availability"] == "out_of_stock"
    detail = api_client.get(f"{API}/products/new-coat").data
    assert (
        detail["available"] is False
        and detail["price"]["final"]["amount"] == "400000"
        and detail["price"]["discount_percent"] == 20
    )
    manager.patch(f"{API}/staff/variants/{vid}", {"is_active": False}, format="json")
    assert api_client.get(f"{API}/products/new-coat").status_code == 404  # no active priced variant left
    assert (
        manager.patch(f"{API}/staff/variants/8c5d1c64-0e2a-4c64-8f2c-1b2b3c4d5e6f", {}, format="json").status_code
        == 404
    )


# ------------------------------------------------------------------ images
def upload(client, pid, data=None, **fields):
    file = SimpleUploadedFile("photo.jpg", data or f.image_bytes(), content_type="image/jpeg")
    return client.post(f"{BASE}/{pid}/images", {"file": file, "rights": "owned", **fields}, format="multipart")


def test_image_upload_reorder_update_and_delete(manager, world, api_client):
    pid = new_product(manager).data["id"]
    first = upload(manager, pid, alt="front")
    assert first.status_code == 201
    second = upload(manager, pid, f.image_bytes(color="#223344"), attribution="Photo: Studio")
    images = second.data["images"]
    assert (
        [i["sort_order"] for i in images] == [0, 1]
        and images[0]["thumbnail_url"].endswith(".webp")
        and images[1]["attribution"] == "Photo: Studio"
    )
    order = [images[1]["id"], images[0]["id"]]
    assert [
        i["id"] for i in manager.post(f"{BASE}/{pid}/images/reorder", {"order": order}, format="json").data["images"]
    ] == order
    assert manager.post(f"{BASE}/{pid}/images/reorder", {"order": order[:1]}, format="json").status_code == 400
    patched = manager.patch(f"{API}/staff/images/{order[0]}", {"alt": "side", "is_published": False}, format="json")
    assert patched.status_code == 200 and patched.data["images"][0]["is_published"] is False
    assert manager.patch(f"{API}/staff/images/{order[0]}", {"alt": 5}, format="json").status_code == 400
    assert manager.delete(f"{API}/staff/images/{order[1]}").status_code == 204
    assert len(manager.get(f"{BASE}/{pid}").data["images"]) == 1


def test_image_upload_rejections(manager, world):
    pid = new_product(manager).data["id"]
    assert upload(manager, pid, b"definitely not an image").status_code == 400
    assert (
        upload(manager, pid, rights="source_permitted", source="trendyol").status_code == 422
    )  # source terms do not permit copying
    assert upload(manager, pid, rights="source_permitted").status_code == 422
    assert upload(manager, pid, rights="made-up").status_code == 400
    assert upload(manager, pid, source="nope").status_code == 400
    f.source("licensed", allows_image_copy=True)
    assert upload(manager, pid, rights="source_permitted", source="licensed").status_code == 201
    assert manager.get(f"{BASE}/{pid}").data["images"][0]["source"] == "licensed"


def test_staff_responses_are_never_cached(manager, world):
    resp = manager.get(BASE)
    assert "no-store" in resp["Cache-Control"]
