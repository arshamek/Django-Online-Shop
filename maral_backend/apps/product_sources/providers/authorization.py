"""
The authorisation gate. A live provider call is refused unless the source row records *why* we may talk
to it. This does not prove a licence exists; it makes "we have one" an explicit, auditable statement.
"""

from ..models import IntegrationType, ProductSource
from .errors import ProviderNotAuthorized

AUTHORIZED_TYPES = frozenset({IntegrationType.OFFICIAL_API, IntegrationType.LICENSED_FEED})


def authorization_problems(source: ProductSource) -> list[str]:
    problems = []
    if not source.is_active:
        problems.append("the source is inactive")
    if source.integration_type not in AUTHORIZED_TYPES:
        problems.append("integration_type must be official_api or licensed_feed")
    if not (source.terms_note or "").strip():
        problems.append("terms_note (the licence / permission record) is empty")
    return problems


def assert_source_authorized(source: ProductSource) -> None:
    problems = authorization_problems(source)
    if problems:
        raise ProviderNotAuthorized(f"Live access to '{source.code}' is not authorised: " + "; ".join(problems) + ".")
