from django.conf import settings
from django.db import models


class AppendOnlyError(Exception):
    pass


class AuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyError("Audit log entries cannot be modified.")

    def delete(self):
        raise AppendOnlyError("Audit log entries cannot be deleted.")


class AuditLog(models.Model):
    """
    Append-only record of security-relevant events. Contains ids and coarse metadata only:
    never passwords, tokens, OTP codes or full identifiers. Production should additionally
    use a DB role without UPDATE/DELETE on this table (decision D-5).
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    action = models.CharField(max_length=64, db_index=True)  # e.g. auth.login.failed
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_public_id = models.UUIDField(null=True, blank=True)  # survives user deletion
    target_type = models.CharField(max_length=32, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    request_id = models.CharField(max_length=128, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        db_table = "audit_log"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["target_type", "target_id"], name="audit_target_idx")]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise AppendOnlyError("Audit log entries cannot be modified.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Audit log entries cannot be deleted.")
