"""Structured logging: JSON in production, readable console in development."""

import logging
import re

import structlog

from .context import request_id_var, task_id_var

_SENSITIVE_KEY = re.compile(
    r"(pass(word|wd)?|secret|token|authorization|cookie|api[_-]?key|otp|session|csrf|card|cvv|iban)",
    re.IGNORECASE,
)


def add_correlation_context(logger, method_name, event_dict):
    request_id = request_id_var.get()
    task_id = task_id_var.get()
    if request_id:
        event_dict.setdefault("request_id", request_id)
    if task_id:
        event_dict.setdefault("task_id", task_id)
    return event_dict


def redact_sensitive(logger, method_name, event_dict):
    """Mask values of keys that look like credentials/PII. Never log request bodies or headers."""
    for key in list(event_dict):
        if _SENSITIVE_KEY.search(key):
            event_dict[key] = "[REDACTED]"
    return event_dict


def _shared_processors() -> list:
    return [
        add_correlation_context,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_sensitive,
    ]


def configure_structlog() -> None:
    structlog.configure(
        processors=[*_shared_processors(), structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def build_logging_config(*, level: str = "INFO", json_logs: bool = True) -> dict:
    if json_logs:
        renderer_chain = [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ]
    else:
        renderer_chain = [structlog.dev.ConsoleRenderer(colors=False)]
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structured": {
                "()": structlog.stdlib.ProcessorFormatter,
                "foreign_pre_chain": _shared_processors(),
                "processors": [structlog.stdlib.ProcessorFormatter.remove_processors_meta, *renderer_chain],
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "structured",
                "stream": "ext://sys.stdout",
            }
        },
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            # Our request middleware already emits one access-log event per request.
            "django.server": {"level": "WARNING"},
            "django.request": {"level": "ERROR"},
            "celery.utils.functional": {"level": "WARNING"},
        },
    }


def get_logger(name: str | None = None):
    return structlog.get_logger(name)


__all__ = ["build_logging_config", "configure_structlog", "get_logger", "logging"]
