from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.common"
    label = "common"

    def ready(self):
        from . import celery_signals  # noqa: F401 - correlation ids across Celery
        from .api import schema  # noqa: F401 - registers the OpenAPI auth extension
