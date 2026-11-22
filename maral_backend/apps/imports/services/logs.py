import structlog

from apps.common.logging_config import redact_sensitive

from ..models import ImportJob, ImportJobItem, ImportLog, LogLevel

logger = structlog.get_logger("maral.imports")


def log(job: ImportJob, level: str, message: str, *, item: ImportJobItem | None = None, **context) -> ImportLog:
    """Append a job log row. Context is redacted; never pass raw payloads, headers or credentials."""
    clean = redact_sensitive(None, "", dict(context))
    clean = {
        k: (v if isinstance(v, (str, int, float, bool, type(None), list, dict)) else str(v)) for k, v in clean.items()
    }
    getattr(logger, "error" if level == LogLevel.ERROR else "info")(
        "import_job", job=str(job.public_id), msg=message, **clean
    )
    return ImportLog.objects.create(job=job, item=item, level=level, message=message[:500], context=clean)
