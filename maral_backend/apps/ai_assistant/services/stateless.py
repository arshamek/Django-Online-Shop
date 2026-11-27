"""Stateless endpoints (recommendations, compare): same discovery/pricing/ranking as the chat, but no conversation is stored."""

from apps.common.exceptions import BusinessRuleViolation

from ..constants import SUPPORTED_LANGUAGES
from ..models import AssistantConfig, UsageStatus
from . import limits, nlu, recommend
from .chat import AssistantDisabled, _apply_request_overrides, _build_spec, _load
from .discovery import discover
from .providers.factory import resolve
from .sources import internal
from .sources.base import SearchSpec


def _prepare(user):
    config = AssistantConfig.load()
    choice = resolve(config)
    if not config.enabled:
        limits.record(user, provider=choice.provider.name, status=UsageStatus.DISABLED, demo_mode=choice.demo_mode)
        raise AssistantDisabled()
    limits.check_daily(user, config)
    return config, choice


def recommendations(request, user, payload: dict) -> dict:
    config, choice = _prepare(user)
    language = payload.get("language") if payload.get("language") in SUPPORTED_LANGUAGES else None
    parsed = nlu.parse_message(payload.get("query") or "", fallback_language=language or "fa", forced_language=language)
    if parsed.budget.ambiguous:
        limits.record(user, provider="none", demo_mode=choice.demo_mode)
        return {
            "language": parsed.language,
            "products": [],
            "provider_status": [],
            "applied_filters": None,
            "relaxed_filters": [],
            "clarification": {
                "reason": parsed.budget.reason,
                "options": ["toman", "rial"] if parsed.budget.reason == "currency_unclear" else ["toman"],
            },
            "demo_mode": choice.demo_mode,
            "assumptions": [],
        }
    spec, f = _build_spec(parsed, {}, config, parsed.language)
    _apply_request_overrides(payload, spec, f)
    if not spec.has_terms():
        raise BusinessRuleViolation(
            "Describe the product (category, brand or keywords).", code="need_product_description"
        )
    ctx = internal.make_context(request, parsed.language)
    result = discover(spec, ctx, config)
    limits.record(user, provider="none", demo_mode=choice.demo_mode)
    return {
        "language": parsed.language,
        "products": result.products,
        "provider_status": [r.as_dict() for r in result.reports],
        "applied_filters": spec.as_public(),
        "relaxed_filters": result.relaxed,
        "clarification": None,
        "demo_mode": choice.demo_mode,
        "assumptions": list(parsed.budget.assumptions),
    }


def compare(request, user, payload: dict) -> dict:
    config, choice = _prepare(user)
    language = payload.get("language") if payload.get("language") in SUPPORTED_LANGUAGES else "fa"
    ctx = internal.make_context(request, language)
    cands = _load(list(payload["product_references"]), SearchSpec(language=language), ctx, config)
    if len(cands) < 2:
        raise BusinessRuleViolation(
            "At least two of the referenced products must exist to compare.", code="compare_needs_two"
        )
    limits.record(user, provider="none", demo_mode=choice.demo_mode)
    return {
        "language": language,
        "products": [c.data for c in cands],
        "comparison": recommend.compare(cands),
        "demo_mode": choice.demo_mode,
    }
