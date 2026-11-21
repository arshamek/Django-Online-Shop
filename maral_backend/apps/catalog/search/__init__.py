from django.conf import settings
from django.utils.module_loading import import_string

from .base import SearchBackend

DEFAULT_BACKEND = "apps.catalog.search.database.DatabaseSearchBackend"


def get_search_backend() -> SearchBackend:
    return import_string(getattr(settings, "CATALOG_SEARCH_BACKEND", DEFAULT_BACKEND))()
