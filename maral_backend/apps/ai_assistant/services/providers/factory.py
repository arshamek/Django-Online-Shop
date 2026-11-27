"""Chooses the provider from the staff config + environment. Never reports a live provider that isn't configured."""

from dataclasses import dataclass

from django.conf import settings

from ...models import AssistantConfig, AssistantProvider
from .anthropic import AnthropicProvider
from .base import AIProviderInterface
from .mock import MockAssistantProvider


@dataclass
class ProviderChoice:
    provider: AIProviderInterface
    demo_mode: bool
    configured: bool
    verified: bool
    note: str = ""

    def public(self) -> dict:
        return {
            "name": self.provider.name,
            "model": self.provider.model,
            "mode": "demo" if self.demo_mode else "live",
            "configured": self.configured,
            "verified": self.verified,
            "note": self.note or None,
        }


def resolve(config: AssistantConfig) -> ProviderChoice:
    wanted = config.provider
    if wanted == AssistantProvider.ANTHROPIC:
        key = settings.AI_SETTINGS.get("api_key", "")
        model = config.model or settings.AI_SETTINGS.get("model", "")
        if key and model:
            verified = bool(config.last_provider_test_ok)
            note = "" if verified else "credentials_present_but_no_successful_provider_test_yet"
            return ProviderChoice(
                AnthropicProvider(model, key), demo_mode=False, configured=True, verified=verified, note=note
            )
        return ProviderChoice(
            MockAssistantProvider(),
            demo_mode=True,
            configured=False,
            verified=False,
            note="anthropic_selected_but_credentials_or_model_missing_demo_mode_used",
        )
    return ProviderChoice(
        MockAssistantProvider(),
        demo_mode=True,
        configured=True,
        verified=False,
        note="demo_mode_deterministic_assistant",
    )
