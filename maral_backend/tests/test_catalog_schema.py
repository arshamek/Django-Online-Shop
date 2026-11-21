import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.catalog import serializers as s

from .conftest import API

pytestmark = pytest.mark.django_db


def assert_matches(serializer_cls, payload: dict, path: str = ""):
    """Documented fields == real fields (optional ones may be absent)."""
    fields = serializer_cls().fields
    assert set(payload) <= set(fields), f"{path}: undocumented keys {set(payload) - set(fields)}"
    required = {n for n, fld in fields.items() if fld.required}
    assert required <= set(payload), f"{path}: documented-but-missing keys {required - set(payload)}"
    for name, fld in fields.items():
        value = payload.get(name)
        child = getattr(fld, "child", None)
        if isinstance(value, dict) and hasattr(fld, "fields"):
            assert_matches(type(fld), value, f"{path}.{name}")
        elif isinstance(value, list) and value and hasattr(child, "fields") and isinstance(value[0], dict):
            for item in value:
                assert_matches(type(child), item, f"{path}.{name}[]")


def test_card_and_detail_responses_match_their_documented_schema(demo_catalog):
    client = APIClient()
    card = client.get(f"{API}/products", {"page_size": 3}).data["results"]
    for item in card:
        assert_matches(s.ProductCardSerializer, item, "card")
    detail = client.get(f"{API}/products/belted-wool-coat").data
    assert_matches(s.ProductDetailSerializer, detail, "detail")
    assert_matches(s.ProductCardSerializer, detail["related"][0], "related")
    assert detail["variants"] and all(v["price"].keys() >= {"final", "breakdown"} for v in detail["variants"])


def test_openapi_documents_every_storefront_and_staff_route(tmp_path):
    out = tmp_path / "schema.yml"
    call_command("spectacular", file=str(out), validate=True)
    text = out.read_text()
    for path in (
        "/api/v1/storefront/homepage",
        "/api/v1/categories",
        "/api/v1/brands",
        "/api/v1/products",
        "/api/v1/products/search",
        "/api/v1/products/filters",
        "/api/v1/products/recently-viewed",
        "/api/v1/products/related/{ref}",
        "/api/v1/products/{slug}",
        "/api/v1/staff/products",
        "/api/v1/staff/products/{id}/images",
        "/api/v1/staff/variants/{id}",
    ):
        assert f"  {path}:" in text, path
        assert f"  {path}/:" not in text, f"{path} documented with a trailing slash"
    assert "storefront" in text and "staff" in text


def test_no_pending_model_changes():
    call_command("makemigrations", check=True, dry_run=True, verbosity=0)
