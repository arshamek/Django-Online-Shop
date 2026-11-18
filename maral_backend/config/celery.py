"""Celery application. Settings live in Django settings under the CELERY_ namespace."""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

app = Celery("maral")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
