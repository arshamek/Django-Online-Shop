import io

import pytest
from PIL import Image
from rest_framework.test import APIClient

from apps.catalog.models import ImageRights, ProductImage
from apps.catalog.services import images as svc
from apps.common.exceptions import BusinessRuleViolation, DomainError

from . import catalog_factories as f
from .conftest import API

pytestmark = pytest.mark.django_db


def card_images(slug="p"):
    return APIClient().get(f"{API}/products/{slug}").data["images"]


def test_upload_is_validated_reencoded_and_gets_a_webp_thumbnail():
    obj = f.product("p")
    image = svc.add_product_image(obj, f.image_bytes(size=(1200, 1500)), rights="owned", alt="Front view")
    assert (image.width, image.height) == (1200, 1500) and image.checksum
    with Image.open(image.thumbnail.open("rb")) as thumb:
        assert thumb.format == "WEBP" and thumb.width == svc.THUMBNAIL_WIDTH and thumb.height == 600
    with Image.open(image.image.open("rb")) as full:
        assert full.format == "JPEG" and full.size == (1200, 1500)


def test_exif_metadata_is_stripped():
    from PIL import Image as PILImage

    buffer = io.BytesIO()
    img = PILImage.new("RGB", (50, 50), "red")
    exif = img.getexif()
    exif[0x010F] = "SecretCameraMaker"
    exif[0x8825] = {1: "N", 2: (35.0, 41.0, 0.0)}
    img.save(buffer, "JPEG", exif=exif)
    assert b"SecretCameraMaker" in buffer.getvalue()
    stored = svc.add_product_image(f.product("p"), buffer.getvalue(), rights="owned")
    assert b"SecretCameraMaker" not in stored.image.open("rb").read()


def test_small_images_are_not_upscaled_for_the_thumbnail():
    image = svc.add_product_image(f.product("p"), f.image_bytes(size=(200, 250)), rights="owned")
    with Image.open(image.thumbnail.open("rb")) as thumb:
        assert thumb.size == (200, 250)


@pytest.mark.parametrize(
    "data,code",
    [(b"not an image at all", "image_invalid"), (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image_invalid")],
)
def test_invalid_files_are_rejected(data, code):
    with pytest.raises(DomainError) as exc:
        svc.add_product_image(f.product("p"), data, rights="owned")
    assert exc.value.get_codes() == code


def test_gif_is_not_an_accepted_format():
    buffer = io.BytesIO()
    Image.new("P", (10, 10)).save(buffer, "GIF")
    with pytest.raises(DomainError) as exc:
        svc.add_product_image(f.product("p"), buffer.getvalue(), rights="owned")
    assert exc.value.get_codes() == "image_format_unsupported"


def test_oversized_files_and_pixel_bombs_are_rejected(settings):
    settings.CATALOG_IMAGE_MAX_BYTES = 1000
    with pytest.raises(DomainError) as big:
        svc.add_product_image(f.product("a"), f.image_bytes(size=(600, 600)), rights="owned")
    assert big.value.get_codes() == "image_too_large"
    settings.CATALOG_IMAGE_MAX_BYTES = 5_000_000
    settings.CATALOG_IMAGE_MAX_PIXELS = 10_000
    with pytest.raises(DomainError) as pixels:
        svc.add_product_image(f.product("b"), f.image_bytes(size=(200, 200)), rights="owned")
    assert pixels.value.get_codes() == "image_too_large"


def test_a_rights_record_is_mandatory():
    with pytest.raises(DomainError):
        svc.add_product_image(f.product("p"), f.image_bytes(), rights="")
    with pytest.raises(DomainError):
        svc.add_product_image(f.product("q"), f.image_bytes(), rights="whatever")


def test_source_images_are_copied_only_when_the_source_terms_permit_it():
    obj = f.product("p")
    restricted = f.source("trendyol")
    with pytest.raises(BusinessRuleViolation) as exc:
        svc.add_product_image(
            obj,
            f.image_bytes(),
            rights=ImageRights.SOURCE_PERMITTED,
            source=restricted,
            origin_url="https://cdn.example/x.jpg",
        )
    assert exc.value.get_codes() == "image_copy_not_permitted"
    with pytest.raises(BusinessRuleViolation):
        svc.add_product_image(obj, f.image_bytes(), rights=ImageRights.SOURCE_PERMITTED, source=None)
    assert not obj.images.exists()
    licensed = f.source("licensed-feed", allows_image_copy=True, terms_note="Written licence 2026-01")
    stored = svc.add_product_image(
        obj, f.image_bytes(), rights=ImageRights.SOURCE_PERMITTED, source=licensed, attribution="Photo: Licensed Feed"
    )
    assert stored.source == licensed and svc.source_permits_copy(licensed) and not svc.source_permits_copy(restricted)


def test_model_validation_mirrors_the_service_rule():
    from django.core.exceptions import ValidationError

    obj = f.product("p")
    with pytest.raises(ValidationError):
        ProductImage(product=obj, rights=ImageRights.SOURCE_PERMITTED, source=f.source("trendyol")).clean()


def test_the_service_never_touches_the_network(monkeypatch):
    import socket

    def boom(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket, "create_connection", boom)
    svc.add_product_image(
        f.product("p"), f.image_bytes(), rights="owned", origin_url="https://cdn.example/never-fetched.jpg"
    )


def test_order_and_alt_text_and_attribution_are_served():
    obj = f.product("p", names={"fa": "نام محصول"})
    svc.add_product_image(obj, f.image_bytes(color="#111111"), rights="owned", alt="", sort_order=2)
    svc.add_product_image(
        obj, f.image_bytes(color="#222222"), rights="licensed", alt="نمای جلو", attribution="عکس: استودیو", sort_order=0
    )
    svc.add_product_image(obj, f.image_bytes(color="#333333"), rights="owned", sort_order=1)
    images = card_images()
    assert [i["alt"] for i in images] == [
        "نمای جلو",
        "نام محصول",
        "نام محصول",
    ]  # ordered; empty alt falls back to the title
    assert images[0]["attribution"] == "عکس: استودیو" and images[1]["attribution"] is None
    assert all(i["url"].startswith("http") and i["thumbnail_url"].endswith(".webp") for i in images)


def test_unpublished_and_fileless_images_are_never_served_and_origin_url_never_leaks():
    obj = f.product("p")
    svc.add_product_image(
        obj, f.image_bytes(), rights="owned", is_published=False, origin_url="https://secret-source.example/a.jpg"
    )
    ProductImage.objects.create(product=obj, rights="owned", image="", origin_url="https://hotlink.example/b.jpg")
    kept = svc.add_product_image(obj, f.image_bytes(color="#abcdef"), rights="owned")
    body = APIClient().get(f"{API}/products/p")
    assert [i["id"] for i in body.data["images"]] == [str(kept.public_id)]
    assert b"secret-source" not in body.content and b"hotlink" not in body.content


def test_reorder_requires_every_image_exactly_once():
    obj = f.product("p")
    a = svc.add_product_image(obj, f.image_bytes(color="#010101"), rights="owned")
    b = svc.add_product_image(obj, f.image_bytes(color="#020202"), rights="owned")
    svc.reorder_images(obj, [b.public_id, a.public_id])
    assert [i.pk for i in obj.images.order_by("sort_order")] == [b.pk, a.pk]
    with pytest.raises(DomainError):
        svc.reorder_images(obj, [a.public_id])
    with pytest.raises(DomainError):
        svc.reorder_images(obj, [a.public_id, a.public_id])


def test_files_live_in_the_configured_storage_backend(settings):
    from django.core.files.storage import default_storage

    image = svc.add_product_image(f.product("p"), f.image_bytes(), rights="owned")
    assert default_storage.exists(image.image.name) and default_storage.exists(image.thumbnail.name)
    assert image.image.name.startswith("products/")


def test_s3_storage_is_configurable_from_the_environment():
    from config.settings import base

    assert {"backend", "bucket_name", "endpoint_url", "custom_domain"} <= set(base.STORAGE_SETTINGS)
