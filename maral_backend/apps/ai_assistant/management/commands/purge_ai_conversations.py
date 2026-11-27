from django.core.management.base import BaseCommand

from apps.ai_assistant.services import retention


class Command(BaseCommand):
    help = "Delete AI assistant conversations idle for longer than AssistantConfig.retention_days."

    def handle(self, *args, **options):
        self.stdout.write(f"Deleted {retention.purge_expired()} conversation(s).")
