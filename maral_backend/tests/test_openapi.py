from drf_spectacular.generators import SchemaGenerator


def _schema():
    return SchemaGenerator().get_schema(request=None, public=True)


def test_schema_contains_versioned_root_and_infrastructure_paths():
    paths = _schema()["paths"]
    assert {"/api/v1/", "/health/", "/ready/"} <= set(paths)


def test_error_envelope_declared_and_attached():
    schema = _schema()
    assert "ErrorEnvelope" in schema["components"]["schemas"]
    responses = schema["paths"]["/api/v1/"]["get"]["responses"]
    assert {"200", "429", "500"} <= set(responses)
    assert responses["429"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ErrorEnvelope"}


def test_schema_and_docs_endpoints_load(api_client):
    assert api_client.get("/api/schema/").status_code == 200
    docs = api_client.get("/api/docs/")
    assert docs.status_code == 200 and b"swagger" in docs.content.lower()
    assert api_client.get("/api/redoc/").status_code == 200


def test_docs_disabled_by_setting():
    import importlib

    from django.test import override_settings
    from django.urls import clear_url_caches

    with override_settings(API_DOCS_ENABLED=False, ROOT_URLCONF="config.urls"):
        import config.urls

        importlib.reload(config.urls)
        clear_url_caches()
        from django.test import Client

        assert Client().get("/api/docs/").status_code == 404
    importlib.reload(config.urls)
    clear_url_caches()
