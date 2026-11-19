import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.accounts.models import Address
from tests.conftest import API

pytestmark = pytest.mark.django_db

IR = {
    "label": "خانه",
    "recipient_name": "سارا محمدی",
    "phone": "۰۹۱۲۱۲۳۴۵۶۷",
    "country": "ir",
    "province": "تهران",
    "city": "تهران",
    "district": "ولنجک",
    "address_line1": "خیابان ولیعصر، کوچه نمونه، پلاک ۱۲",
    "address_line2": "واحد ۴",
    "postal_code": "۱۲۳۴۵۶۷۸۹۰",
    "delivery_instructions": "زنگ واحد ۴ را بزنید",
}


def png(size=(800, 600), mode="RGB", fmt="PNG", exif=False):
    buf = io.BytesIO()
    img = Image.new(mode, size, (200, 30, 30) if mode == "RGB" else (200, 30, 30, 128))
    kwargs = {}
    if exif:
        e = Image.Exif()
        e[0x010F] = "SecretCameraMaker"
        kwargs["exif"] = e
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


# ---- profile ------------------------------------------------------------------------------
def test_get_me_shape(make_user, login):
    client = login(make_user(first_name="Sara", last_name="M"))
    body = client.get(f"{API}/me").json()
    assert {
        "id",
        "email",
        "phone",
        "first_name",
        "last_name",
        "full_name",
        "status",
        "email_verified",
        "phone_verified",
        "created_at",
        "last_login",
        "profile",
    } <= set(body)
    assert body["profile"] == {"avatar_url": None, "birth_date": None, "language": "fa", "currency": "IRT"}
    assert "password" not in body and body["roles"] == [] and body["permissions"] == []


def test_patch_profile_and_preferences(make_user, login):
    client = login(make_user())
    r = client.patch(
        f"{API}/me",
        {"first_name": " علي ", "last_name": "كريمي", "language": "tr", "currency": "TRY", "birth_date": "1990-03-06"},
        format="json",
    )
    body = r.json()
    assert r.status_code == 200
    assert (body["first_name"], body["last_name"]) == ("علی", "کریمی")
    assert (
        body["profile"]["language"] == "tr"
        and body["profile"]["currency"] == "TRY"
        and body["profile"]["birth_date"] == "1990-03-06"
    )


@pytest.mark.parametrize(
    "payload,field",
    [({"language": "xx"}, "language"), ({"currency": "BTC"}, "currency"), ({"birth_date": "2999-01-01"}, "birth_date")],
)
def test_patch_profile_validation(make_user, login, payload, field):
    r = login(make_user()).patch(f"{API}/me", payload, format="json")
    assert r.status_code == 400 and field in r.json()["error"]["fields"]


def test_patch_cannot_escalate_or_change_identifiers(make_user, login):
    user = make_user()
    client = login(user)
    r = client.patch(
        f"{API}/me",
        {
            "is_staff": True,
            "is_superuser": True,
            "status": "active",
            "email": "x@evil.com",
            "email_verified": True,
            "roles": ["Super Admin"],
        },
        format="json",
    )
    assert r.status_code == 200
    user.refresh_from_db()
    assert not user.is_staff and not user.is_superuser and user.email != "x@evil.com" and not user.email_verified


# ---- avatar -------------------------------------------------------------------------------
def test_avatar_upload_is_reencoded_and_stripped(make_user, login, settings):
    client = login(make_user())
    upload = SimpleUploadedFile("me.png", png((1200, 700), exif=False), content_type="image/png")
    r = client.put(f"{API}/me/avatar", {"avatar": upload}, format="multipart")
    assert r.status_code == 200
    url = r.json()["profile"]["avatar_url"]
    assert url.startswith("http") and url.endswith(".webp") and "me.png" not in url
    from apps.accounts.models import CustomerProfile

    stored = CustomerProfile.objects.get().avatar
    image = Image.open(stored.open())
    assert image.format == "WEBP" and image.size == (512, 512)
    assert not image.getexif()


def test_avatar_jpeg_exif_removed(make_user, login):
    client = login(make_user())
    upload = SimpleUploadedFile("a.jpg", png((300, 300), fmt="JPEG", exif=True), content_type="image/jpeg")
    assert client.put(f"{API}/me/avatar", {"avatar": upload}, format="multipart").status_code == 200
    from apps.accounts.models import CustomerProfile

    raw = CustomerProfile.objects.get().avatar.open().read()
    assert b"SecretCameraMaker" not in raw


def test_avatar_replace_deletes_old_file_and_delete(make_user, login):
    client = login(make_user())
    from apps.accounts.models import CustomerProfile

    client.put(f"{API}/me/avatar", {"avatar": SimpleUploadedFile("a.png", png())}, format="multipart")
    first = CustomerProfile.objects.get().avatar
    first_name, storage = first.name, first.storage
    client.put(f"{API}/me/avatar", {"avatar": SimpleUploadedFile("b.png", png((100, 100)))}, format="multipart")
    assert not storage.exists(first_name)
    assert client.delete(f"{API}/me/avatar").status_code == 204
    assert client.get(f"{API}/me").json()["profile"]["avatar_url"] is None


@pytest.mark.parametrize(
    "name,data",
    [
        ("evil.png", b"<?php system($_GET['c']); ?>"),
        ("fake.png", b"\x89PNG\r\n\x1a\n" + b"garbage"),
        ("vector.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"),
        ("anim.gif", None),
    ],
)
def test_avatar_rejects_non_images_and_disallowed_formats(make_user, login, name, data):
    if data is None:
        data = png(fmt="GIF")
    r = login(make_user()).put(f"{API}/me/avatar", {"avatar": SimpleUploadedFile(name, data)}, format="multipart")
    assert r.status_code == 400 and "avatar" in r.json()["error"]["fields"]


def test_avatar_size_and_dimension_limits(make_user, login, settings):
    client = login(make_user())
    settings.AVATAR_MAX_BYTES = 1000
    r = client.put(f"{API}/me/avatar", {"avatar": SimpleUploadedFile("a.png", png((600, 600)))}, format="multipart")
    assert r.status_code == 400
    settings.AVATAR_MAX_BYTES = 2 * 1024 * 1024
    settings.AVATAR_MAX_PIXELS = 100
    r = client.put(f"{API}/me/avatar", {"avatar": SimpleUploadedFile("a.png", png((50, 50)))}, format="multipart")
    assert r.status_code == 400


# ---- addresses ----------------------------------------------------------------------------
def test_empty_address_list(make_user, login):
    r = login(make_user()).get(f"{API}/me/addresses")
    assert r.status_code == 200 and r.json() == {
        "count": 0,
        "page": 1,
        "page_size": 24,
        "next": None,
        "previous": None,
        "results": [],
    }


def test_create_persian_address_normalised(make_user, login):
    client = login(make_user())
    r = client.post(f"{API}/me/addresses", IR, format="json")
    body = r.json()
    assert r.status_code == 201 and r["Location"].endswith(body["id"])
    assert body["phone"] == "+989121234567" and body["country"] == "IR" and body["postal_code"] == "1234567890"
    assert body["is_default"] is True  # first address
    assert body["city"] == "تهران" and "پلاک ۱۲" in body["address_line1"]


@pytest.mark.parametrize(
    "country,postal,ok",
    [
        ("IR", "1234567890", True),
        ("IR", "12345", False),
        ("TR", "34000", True),
        ("TR", "3400", False),
        ("US", "94105-1234", True),
        ("US", "9410", False),
        ("GB", "SW1A 1AA", True),
        ("DE", "10115", True),
        ("AE", "", True),
        ("FR", "", False),
        ("NL", "1012 AB", True),
    ],
)
def test_international_postal_codes(make_user, login, country, postal, ok):
    data = {
        **IR,
        "country": country,
        "postal_code": postal,
        "phone": "+905321234567" if country != "IR" else IR["phone"],
    }
    r = login(make_user()).post(f"{API}/me/addresses", data, format="json")
    assert (r.status_code == 201) is ok, r.content


def test_address_validation_errors(make_user, login):
    client = login(make_user())
    r = client.post(f"{API}/me/addresses", {"country": "ZZ", "phone": "12"}, format="json")
    fields = r.json()["error"]["fields"]
    assert r.status_code == 400 and {"recipient_name", "city", "address_line1", "country", "phone"} <= set(fields)


def test_default_address_semantics(make_user, login):
    client = login(make_user())
    a = client.post(f"{API}/me/addresses", {**IR, "label": "A"}, format="json").json()
    b = client.post(f"{API}/me/addresses", {**IR, "label": "B"}, format="json").json()
    assert a["is_default"] and not b["is_default"]
    r = client.patch(f"{API}/me/addresses/{b['id']}", {"is_default": True}, format="json")
    assert r.json()["is_default"] is True
    listing = client.get(f"{API}/me/addresses").json()["results"]
    assert [x["is_default"] for x in listing].count(True) == 1 and listing[0]["id"] == b["id"]
    c = client.post(f"{API}/me/addresses", {**IR, "label": "C", "is_default": True}, format="json").json()
    assert Address.objects.filter(is_default=True).count() == 1 and c["is_default"]
    r = client.patch(f"{API}/me/addresses/{c['id']}", {"is_default": False}, format="json")
    assert r.status_code == 400  # must pick another default instead


def test_deleting_default_promotes_newest(make_user, login):
    client = login(make_user())
    a = client.post(f"{API}/me/addresses", {**IR, "label": "A"}, format="json").json()
    client.post(f"{API}/me/addresses", {**IR, "label": "B"}, format="json")
    c = client.post(f"{API}/me/addresses", {**IR, "label": "C"}, format="json").json()
    assert client.delete(f"{API}/me/addresses/{a['id']}").status_code == 204
    defaults = Address.objects.filter(is_default=True)
    assert defaults.count() == 1 and defaults.get().label == "C" and str(defaults.get().public_id) == c["id"]
    for addr in client.get(f"{API}/me/addresses").json()["results"]:
        client.delete(f"{API}/me/addresses/{addr['id']}")
    assert client.get(f"{API}/me/addresses").json()["count"] == 0


def test_update_address_partial_and_country_change_revalidates_postal(make_user, login):
    client = login(make_user())
    a = client.post(f"{API}/me/addresses", IR, format="json").json()
    r = client.patch(f"{API}/me/addresses/{a['id']}", {"city": "کرج", "delivery_instructions": ""}, format="json")
    assert r.status_code == 200 and r.json()["city"] == "کرج" and r.json()["postal_code"] == "1234567890"
    r = client.patch(f"{API}/me/addresses/{a['id']}", {"country": "US"}, format="json")
    assert r.status_code == 400 and "postal_code" in r.json()["error"]["fields"]


def test_address_limit(make_user, login, settings):
    settings.MAX_ADDRESSES_PER_USER = 2
    client = login(make_user())
    codes = [client.post(f"{API}/me/addresses", IR, format="json").status_code for _ in range(3)]
    assert codes == [201, 201, 422]
    assert client.post(f"{API}/me/addresses", IR, format="json").json()["error"]["code"] == "address_limit_reached"


def test_address_pagination(make_user, login):
    client = login(make_user())
    for _ in range(5):
        client.post(f"{API}/me/addresses", IR, format="json")
    body = client.get(f"{API}/me/addresses?page_size=2").json()
    assert body["count"] == 5 and len(body["results"]) == 2 and body["next"]
