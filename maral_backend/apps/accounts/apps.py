from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _sync_roles_after_migrate(sender, using="default", **kwargs):
    from django.contrib.auth.management import create_permissions
    from django.contrib.contenttypes.management import create_contenttypes

    from .roles import sync_roles

    create_contenttypes(sender, using=using, verbosity=0)
    create_permissions(sender, using=using, verbosity=0)
    sync_roles()


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"

    def ready(self):
        from . import signals  # noqa: F401

        post_migrate.connect(_sync_roles_after_migrate, sender=self, dispatch_uid="accounts.sync_roles")
