"""
Message delivery. No provider is assumed to exist: email goes through Django's email backend
(configure SMTP), SMS through a pluggable backend that defaults to *disabled* in production.
"""

import structlog
from django.conf import settings
from django.utils.module_loading import import_string

from .normalizers import mask_phone

logger = structlog.get_logger("maral.delivery")


class ProviderUnavailable(Exception):
    """No SMS provider is configured (or it is down)."""


class SMSBackend:
    def send(self, phone: str, message: str) -> None:
        raise NotImplementedError


class DisabledSMSBackend(SMSBackend):
    """Production default until a real provider backend is implemented and configured."""

    def send(self, phone: str, message: str) -> None:
        raise ProviderUnavailable("No SMS provider is configured.")


class ConsoleSMSBackend(SMSBackend):
    """Development: print the message (including the OTP) to the log."""

    def send(self, phone: str, message: str) -> None:
        logger.info("sms_console", to=mask_phone(phone), message=message)


class LocmemSMSBackend(SMSBackend):
    """Tests: collect messages in `LocmemSMSBackend.outbox`."""

    outbox: list[tuple[str, str]] = []

    def send(self, phone: str, message: str) -> None:
        type(self).outbox.append((phone, message))


def send_sms(phone: str, message: str) -> None:
    backend = import_string(settings.SMS_BACKEND)()
    backend.send(phone, message)
    logger.info("sms_sent", to=mask_phone(phone), backend=type(backend).__name__)
