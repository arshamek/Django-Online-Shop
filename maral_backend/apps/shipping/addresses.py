"""
Country-aware delivery address rules (Persian and international formats).

Defaults reproduce the Phase 7 behaviour (every country: name, phone, country, city, address line 1; Iran additionally
its postal code). A `CountryShippingPolicy.address_requirements` override makes the rules configurable per country with
no code change:

    {"required": ["province", "postal_code"], "postal_code_pattern": "^\\d{5}$", "phone_required": false}

Persian/Arabic-Indic digits are accepted everywhere and normalised to ASCII; Arabic yeh/kaf are normalised to Persian.
"""

import re
from dataclasses import dataclass

from django.core.exceptions import ValidationError

from apps.accounts.normalizers import to_ascii_digits
from apps.accounts.validators import normalize_postal_code
from apps.common.exceptions import BusinessRuleViolation

BASE_REQUIRED = ("recipient_name", "phone", "country", "city", "address_line1")
COUNTRY_REQUIRED = {"IR": ("postal_code",)}  # Iranian delivery needs the 10-digit postal code
OPTIONAL_FIELDS = ("province", "district", "address_line2", "postal_code", "delivery_instructions")
KNOWN_FIELDS = set(BASE_REQUIRED) | set(OPTIONAL_FIELDS)
_FIXES = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"})
MAX_PATTERN_LENGTH = 120


@dataclass(frozen=True)
class AddressRequirements:
    country: str
    required: tuple[str, ...]
    postal_code_pattern: str | None = None

    def as_dict(self) -> dict:
        return {
            "country": self.country,
            "required_fields": list(self.required),
            "optional_fields": [f for f in KNOWN_FIELDS if f not in self.required],
            "postal_code_pattern": self.postal_code_pattern,
        }


def _policy(country: str):
    from .models import CountryShippingPolicy

    return CountryShippingPolicy.objects.filter(country=country.upper(), is_active=True).first()


def requirements_for(country: str) -> AddressRequirements:
    country = (country or "").upper()
    required = list(BASE_REQUIRED) + list(COUNTRY_REQUIRED.get(country, ()))
    pattern = None
    policy = _policy(country) if country else None
    override = (policy.address_requirements if policy else {}) or {}
    for field in override.get("required", []):
        if field in KNOWN_FIELDS and field not in required:
            required.append(field)
    for field in override.get("optional", []):
        if field in required and field not in BASE_REQUIRED[:1] + ("country",):
            required.remove(field)
    if override.get("phone_required") is False and "phone" in required:
        required.remove("phone")
    if (
        isinstance(override.get("postal_code_pattern"), str)
        and len(override["postal_code_pattern"]) <= MAX_PATTERN_LENGTH
    ):
        pattern = override["postal_code_pattern"]
    return AddressRequirements(country, tuple(required), pattern)


def normalize_address(data: dict) -> dict:
    """Trim, unify Persian letters in free text, ASCII digits in phone / postal code."""
    out = {}
    for field in KNOWN_FIELDS:
        value = str(data.get(field, "") or "").strip()
        if field in ("phone", "postal_code"):
            value = to_ascii_digits(value)
        elif field != "country":
            value = value.translate(_FIXES)
        out[field] = re.sub(r"[ \t]+", " ", value)
    out["country"] = out["country"].upper()
    return out


def validate(data: dict) -> dict:
    """Raises BusinessRuleViolation(address_incomplete) with per-field messages; returns the normalised address."""
    address = normalize_address(data)
    req = requirements_for(address["country"])
    problems = {f: ["Required."] for f in req.required if not address.get(f)}
    postal = address.get("postal_code", "")
    if postal and "postal_code" not in problems:
        try:
            if req.postal_code_pattern:
                if not re.match(req.postal_code_pattern, postal.upper()):
                    raise ValidationError("pattern")
            else:
                normalize_postal_code(address["country"], postal)
        except ValidationError:
            problems["postal_code"] = ["Enter a valid postal code for this country."]
        except re.error:  # a bad admin pattern must not block checkout silently: fall back to the built-in rule
            try:
                normalize_postal_code(address["country"], postal)
            except ValidationError:
                problems["postal_code"] = ["Enter a valid postal code for this country."]
    if problems:
        raise BusinessRuleViolation("The delivery address is incomplete.", code="address_incomplete", fields=problems)
    return address
