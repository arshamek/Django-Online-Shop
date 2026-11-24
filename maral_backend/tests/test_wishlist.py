import pytest
from rest_framework.test import APIClient

from tests.phase7_helpers import API, make_product, setup_shop

pytestmark = pytest.mark.django_db


@pytest.fixture
def shop():
    setup_shop()


def test_wishlist_requires_sign_in():
    product, _ = make_product({})
    anon = APIClient()
    assert anon.get(f"{API}/wishlist").status_code in (401, 403)
    assert anon.put(f"{API}/wishlist/items/{product.public_id}").status_code in (401, 403)


def test_add_is_idempotent_list_status_and_remove(shop, make_user, login):
    p1, _ = make_product({})
    p2, _ = make_product({})
    c = login(make_user())
    assert c.put(f"{API}/wishlist/items/{p1.public_id}").status_code == 201
    assert c.put(f"{API}/wishlist/items/{p1.public_id}").status_code == 200  # no duplicate
    c.put(f"{API}/wishlist/items/{p2.public_id}")
    listing = c.get(f"{API}/wishlist").json()
    assert [r["id"] for r in listing["results"]] == [str(p2.public_id), str(p1.public_id)]  # newest first
    assert all(r["in_wishlist"] for r in listing["results"])
    assert c.get(f"{API}/wishlist/items/{p1.public_id}").json()["in_wishlist"] is True
    status = c.get(f"{API}/wishlist/status?ids={p1.public_id},{p2.public_id}").json()["results"]
    assert status == {str(p1.public_id): True, str(p2.public_id): True}
    assert c.delete(f"{API}/wishlist/items/{p1.public_id}").status_code == 204
    assert c.delete(f"{API}/wishlist/items/{p1.public_id}").status_code == 204  # idempotent
    assert c.get(f"{API}/wishlist/items/{p1.public_id}").json()["in_wishlist"] is False


def test_no_cross_account_access(shop, make_user, login):
    p, _ = make_product({})
    a, b = login(make_user()), login(make_user())
    a.put(f"{API}/wishlist/items/{p.public_id}")
    assert b.get(f"{API}/wishlist").json()["results"] == []
    assert b.get(f"{API}/wishlist/items/{p.public_id}").json()["in_wishlist"] is False
    b.delete(f"{API}/wishlist/items/{p.public_id}")  # cannot remove a's entry
    assert a.get(f"{API}/wishlist/items/{p.public_id}").json()["in_wishlist"] is True


def test_hidden_product_cannot_be_added(shop, make_user, login):
    import uuid

    c = login(make_user())
    assert c.put(f"{API}/wishlist/items/{uuid.uuid4()}").json()["error"]["code"] == "product_not_found"


def test_move_to_cart_needs_a_real_choice_and_removes_from_wishlist(shop, make_user, login):
    p, _ = make_product({"size": "m", "color": "red"}, {"size": "l", "color": "red"})
    c = login(make_user())
    c.put(f"{API}/wishlist/items/{p.public_id}")
    r = c.post(f"{API}/wishlist/items/{p.public_id}/move-to-cart", {"color": "red"}, format="json")
    assert r.json()["error"]["code"] == "selection_incomplete"  # the server never guesses a size
    r = c.post(f"{API}/wishlist/items/{p.public_id}/move-to-cart", {"size": "l", "color": "red", "quantity": 2}, format="json")
    assert r.status_code == 200 and r.json()["items"][0]["quantity"] == 2 and r.json()["items"][0]["size"]["code"] == "l"
    assert c.get(f"{API}/wishlist/items/{p.public_id}").json()["in_wishlist"] is False


def test_move_requires_membership_and_unavailable_item_fails(shop, make_user, login):
    p, _ = make_product({"available": False})
    c = login(make_user())
    assert c.post(f"{API}/wishlist/items/{p.public_id}/move-to-cart", {}, format="json").json()["error"]["code"] == "not_in_wishlist"
    c.put(f"{API}/wishlist/items/{p.public_id}")
    r = c.post(f"{API}/wishlist/items/{p.public_id}/move-to-cart", {}, format="json")
    assert r.status_code == 409
    assert c.get(f"{API}/wishlist/items/{p.public_id}").json()["in_wishlist"] is True  # failed move keeps the entry
