import re

import phonenumbers
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .normalizers import normalize_text, to_ascii_digits

# Country-specific postal code patterns; unknown countries fall back to a permissive generic rule.
_POSTAL_PATTERNS = {
    "IR": re.compile(r"^\d{10}$"),
    "TR": re.compile(r"^\d{5}$"),
    "DE": re.compile(r"^\d{5}$"),
    "US": re.compile(r"^\d{5}(-\d{4})?$"),
    "GB": re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}$"),
    "CA": re.compile(r"^[A-Z]\d[A-Z]\s?\d[A-Z]\d$"),
    "FR": re.compile(r"^\d{5}$"),
    "AE": re.compile(r"^$|^\d{1,6}$"),  # the UAE has no postal-code system
    "IQ": re.compile(r"^\d{5}$"),
    "AF": re.compile(r"^\d{4}$"),
}
_GENERIC_POSTAL = re.compile(r"^[A-Z0-9][A-Z0-9 \-]{1,10}[A-Z0-9]$")
_NO_POSTAL_REQUIRED = {"AE"}


def validate_country_code(value: str) -> str:
    code = (value or "").strip().upper()
    if code not in phonenumbers.SUPPORTED_REGIONS:
        raise ValidationError(_("Enter a valid ISO 3166-1 alpha-2 country code."), code="invalid_country")
    return code


def normalize_postal_code(country: str, value: str | None) -> str:
    cleaned = to_ascii_digits(normalize_text(value)).upper()
    if country == "IR":
        cleaned = cleaned.replace("-", "").replace(" ", "")
    if not cleaned:
        if country in _NO_POSTAL_REQUIRED:
            return ""
        raise ValidationError(_("Postal code is required."), code="required")
    pattern = _POSTAL_PATTERNS.get(country, _GENERIC_POSTAL)
    if not pattern.match(cleaned):
        raise ValidationError(_("Enter a valid postal code for this country."), code="invalid_postal_code")
    return cleaned
