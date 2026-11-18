from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Verify the database connection and report engine details (SQL Server specifics when applicable)."

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        self.stdout.write(f"Engine: {db['ENGINE']}  Host: {db.get('HOST') or '-'}  Name: {db['NAME']}")
        try:
            connection.ensure_connection()
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
                if connection.vendor == "microsoft":
                    cursor.execute("SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(64)), @@VERSION")
                    version, banner = cursor.fetchone()
                    cursor.execute(
                        "SELECT collation_name, is_read_committed_snapshot_on FROM sys.databases WHERE name = DB_NAME()"
                    )
                    collation, rcsi = cursor.fetchone()
                    self.stdout.write(f"SQL Server {version} | driver: {db['OPTIONS'].get('driver')}")
                    self.stdout.write(f"Collation: {collation} | READ_COMMITTED_SNAPSHOT: {'ON' if rcsi else 'OFF'}")
                    if not rcsi:
                        self.stdout.write(
                            self.style.WARNING(
                                "READ_COMMITTED_SNAPSHOT is OFF - recommended ON (readers block writers "
                                "otherwise). See docs/setup/DATABASE.md."
                            )
                        )
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f"Database connection FAILED: {type(exc).__name__}: {exc}") from exc
        self.stdout.write(self.style.SUCCESS("Database connection OK"))
