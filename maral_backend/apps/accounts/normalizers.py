"""
Canonical forms for identifiers and user-entered text.

* email  -> NFKC, trimmed, lower-cased (unique per account)
* phone  -> E.164 (e.g. +989121234567); Persian/Arabic digits and Iranian local formats accepted
* text   -> NFKC, Arabic Yeh/Kaf mapped to Persian, control characters and tatweel removed
  (ZWNJ is kept: it is meaningful in Persian)
"""

import re
import unicodedata

import phonenumbers
from django.conf import settings
from django.core.validators import validate_email

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_ARABIC_TO_PERSIAN = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک", "ۀ": "هٔ", "ـ": None})
_CONTROL = re.compile(
    r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]"
)
_SPACES = re.compile(r"\s+")


def to_ascii_digits(value: str) -> str:
    return value.translate(_DIGITS)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", value).translate(_ARABIC_TO_PERSIAN)
    return _SPACES.sub(" ", _CONTROL.sub("", value)).strip()


def normalize_email(value: str | None) -> str | None:
    """Return the canonical email or None when empty. Raises django ValidationError if malformed."""
    if value is None:
        return None
    value = unicodedata.normalize("NFKC", value).strip().lower()
    if not value:
        return None
    validate_email(value)
    return value


def normalize_phone(value: str | None, region: str | None = None) -> str | None:
    """Return E.164 or None when empty. Raises ValueError for invalid numbers."""
    if value is None:
        return None
    raw = to_ascii_digits(unicodedata.normalize("NFKC", value)).strip()
    if not raw:
        return None
    try:
        parsed = phonenumbers.parse(raw, region or settings.DEFAULT_PHONE_REGION)
    except phonenumbers.NumberParseException as exc:
        raise ValueError("Enter a valid phone number.") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("Enter a valid phone number.")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def looks_like_email(identifier: str) -> bool:
    return "@" in identifier


def mask_email(email: str | None) -> str | None:
    if not email:
        return None
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    return f"{phone[:4]}***{phone[-2:]}"
