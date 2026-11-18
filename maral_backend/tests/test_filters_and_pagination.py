import django_filters
import pytest
from django.contrib.auth import get_user_model
from django.urls import path
from rest_framework import generics, serializers

pytestmark = pytest.mark.django_db
User = get_user_model()


class UserFilter(django_filters.FilterSet):
    active = django_filters.BooleanFilter(field_name="is_active")
    min_id = django_filters.NumberFilter(field_name="id", lookup_expr="gte")

    class Meta:
        model = User
        fields = ["active", "min_id"]


class _S(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["username"]


class UserList(generics.ListAPIView):
    queryset = User.objects.order_by("id")
    serializer_class = _S
    permission_classes = []
    authentication_classes = []
    filterset_class = UserFilter
    search_fields = ["username"]
    ordering_fields = ["username"]


urlpatterns = [path("t/users/", UserList.as_view())]


@pytest.fixture(autouse=True)
def _urls(settings):
    settings.ROOT_URLCONF = __name__


@pytest.fixture
def users():
    for i in range(30):
        User.objects.create_user(f"user{i:02d}", password="x" * 12)


def test_pagination_shape_defaults(api_client, users):
    body = api_client.get("/t/users/").json()
    assert set(body) == {"count", "page", "page_size", "next", "previous", "results"}
    assert (body["count"], body["page"], body["page_size"]) == (30, 1, 24)
    assert body["previous"] is None and "page=2" in body["next"]
    assert len(body["results"]) == 24


def test_page_size_capped_at_100(api_client, users):
    assert api_client.get("/t/users/?page_size=1000").json()["page_size"] == 100


def test_second_page(api_client, users):
    body = api_client.get("/t/users/?page=2").json()
    assert body["page"] == 2 and len(body["results"]) == 6 and body["next"] is None


def test_out_of_range_page_is_404(api_client, users):
    response = api_client.get("/t/users/?page=99")
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"


def test_filter_applies(api_client, users):
    User.objects.filter(username="user00").update(is_active=False)
    assert api_client.get("/t/users/?active=false").json()["count"] == 1


def test_unknown_filter_rejected(api_client, users):
    response = api_client.get("/t/users/?colour=red")
    body = response.json()
    assert response.status_code == 400
    assert body["error"]["code"] == "invalid_filter"
    assert "colour" in body["error"]["fields"]


def test_invalid_filter_value_rejected(api_client, users):
    response = api_client.get("/t/users/?min_id=abc")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_filter"


def test_reserved_params_allowed(api_client, users):
    assert api_client.get("/t/users/?q=user0&sort=-username&lang=fa&currency=IRT").status_code == 200
