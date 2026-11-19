from django.core.management.base import BaseCommand

from apps.accounts.roles import ROLE_PERMISSIONS, sync_roles


class Command(BaseCommand):
    help = "Create/refresh the staff role groups from apps/accounts/roles.py (idempotent)."

    def handle(self, *args, **options):
        sync_roles()
        for role, codenames in ROLE_PERMISSIONS.items():
            self.stdout.write(f"{role}: {', '.join(codenames)}")
        self.stdout.write(self.style.SUCCESS("Roles synced"))
