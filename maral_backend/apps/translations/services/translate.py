"""Translate one entity into one language: protect -> cache -> provider -> verify -> apply (never over human text)."""

import hashlib
import json

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import TranslationOrigin, TranslationStatus

from .. import masking
from ..errors import TranslationProviderError, TranslationValidationError
from ..models import TranslationCache
from ..providers.base import TranslationRequest
from ..providers.registry import get_provider
from .entities import adapter_for

logger = structlog.get_logger("maral.translations")
_REPLACEABLE = {TranslationStatus.MACHINE, TranslationStatus.NEEDS_REVIEW, TranslationStatus.STALE}


def writable(state, force: bool = False) -> bool:
    """Machine output may only replace missing text or unreviewed machine text. Never human / approved text."""
    if state is None:
        return True
    return state.origin == TranslationOrigin.MACHINE and state.status in _REPLACEABLE


def cache_key(masked_text, src, dst, fingerprint) -> str:
    """The masked text is exactly what the provider sees, so it fully determines the answer."""
    payload = json.dumps([masked_text, src, dst, fingerprint, settings.TRANSLATION_PROMPT_VERSION], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def translate_text(text: str, *, src: str, dst: str, terms: list[str], context: str, provider, stats: dict) -> str:
    masked, originals = masking.protect(text, terms)
    if not masking.needs_translation(masked):
        return text  # a size, a code, a number: nothing to translate
    key = cache_key(masked, src, dst, provider.fingerprint)
    hit = TranslationCache.objects.filter(key=key).first()
    if hit is not None:
        translated_masked = hit.translated_masked
        TranslationCache.objects.filter(pk=hit.pk).update(hit_count=hit.hit_count + 1, last_used_at=timezone.now())
        stats["from_cache"] += 1
    else:
        result = provider.translate(TranslationRequest(masked, src, dst, context))
        translated_masked = result.text.strip()
        stats["chars"] += len(masked)
        masking.restore(translated_masked, originals, masked)  # validate before caching anything
        TranslationCache.objects.get_or_create(
            key=key,
            defaults={
                "source_language": src,
                "target_language": dst,
                "provider": provider.name,
                "model": provider.model,
                "translated_masked": translated_masked,
            },
        )
    restored = masking.restore(translated_masked, originals, masked)
    masking.verify_numbers(text, restored)
    return restored


def translate_entity(entity_type: str, entity_id: int, target: str, *, provider=None, force: bool = False) -> dict:
    """Raises TranslationProviderError / TranslationValidationError; writes nothing unless every needed unit succeeded."""
    adapter = adapter_for(entity_type)
    loaded = adapter.load(entity_id)
    if loaded is None:
        return {"outcome": "skipped", "reason": "no_source_text"}
    if loaded.source_language == target:
        return {"outcome": "skipped", "reason": "same_language"}
    provider = provider or get_provider()
    states = adapter.states(entity_id, target)
    todo, protected_groups = [], []
    for group, keys in loaded.groups.items():
        state = states.get(group)
        if writable(state, force):
            todo.append((group, keys))
        else:
            protected_groups.append(group)
            if state and state.source_hash != loaded.source_hash and state.status != TranslationStatus.STALE:
                adapter.set_status(
                    entity_id, target, group, TranslationStatus.STALE
                )  # keep the text, flag it for review
    stats = {"from_cache": 0, "chars": 0, "units": 0}
    outputs: dict[str, dict[str, str]] = {}
    for group, keys in todo:
        outputs[group] = {}
        for key in keys:
            outputs[group][key] = translate_text(
                loaded.units[key],
                src=loaded.source_language,
                dst=target,
                terms=loaded.protected,
                context=loaded.context.get(key, ""),
                provider=provider,
                stats=stats,
            )
            stats["units"] += 1
    with transaction.atomic():  # all or nothing
        for group, texts in outputs.items():
            adapter.write(
                entity_id,
                target,
                group,
                texts,
                origin=TranslationOrigin.MACHINE,
                status=TranslationStatus.MACHINE,
                source_hash=loaded.source_hash,
                provider=provider.name,
                model=provider.model,
            )
    outcome = "translated" if todo else "skipped"
    return {
        "outcome": outcome,
        "reason": "" if todo else "protected_text",
        "groups_written": [g for g, _ in todo],
        "groups_protected": protected_groups,
        "source_hash": loaded.source_hash,
        "provider": provider.name,
        "model": provider.model,
        **stats,
    }


def save_manual_correction(entity_type: str, entity_id: int, language: str, group: str, texts: dict, *, user) -> None:
    """Staff text always wins: it is stored as human text and is never overwritten by a machine run."""
    adapter = adapter_for(entity_type)
    loaded = adapter.load(entity_id)
    if loaded is None:
        raise TranslationValidationError("no_source_text", "The entity has no source text to correct against.")
    if loaded.source_language == language:
        raise TranslationValidationError(
            "same_language", "Edit the source text through the catalogue, not as a translation."
        )
    allowed = set(loaded.groups.get(group, []))
    if not allowed or set(texts) - allowed:
        raise TranslationValidationError("unknown_fields", "Unknown group or field.")
    if "name" in allowed and not str(texts.get("name", "")).strip():
        raise TranslationValidationError("name_required", "The name cannot be empty.")
    existing = adapter.states(entity_id, language).get(group)
    if existing is None:  # a first manual translation must still carry every field of the group
        texts = {k: texts.get(k, "") for k in allowed}
    adapter.write(
        entity_id,
        language,
        group,
        texts,
        origin=TranslationOrigin.HUMAN,
        status=TranslationStatus.HUMAN,
        source_hash=loaded.source_hash,
        reviewer=user,
    )


def approve(entity_type: str, entity_id: int, language: str, group: str, *, user) -> None:
    adapter = adapter_for(entity_type)
    state = adapter.states(entity_id, language).get(group)
    if state is None:
        raise TranslationValidationError("nothing_to_approve", "There is no translation to approve.")
    adapter.set_status(entity_id, language, group, TranslationStatus.APPROVED, reviewer=user)


__all__ = ["translate_entity", "save_manual_correction", "approve", "writable", "TranslationProviderError"]
