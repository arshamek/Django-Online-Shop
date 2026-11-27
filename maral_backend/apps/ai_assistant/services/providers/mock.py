"""Deterministic demo assistant. Phrases the backend's real results with the same templates used as the fallback."""

from .base import AIProviderInterface, AIReply, AIRequest


class MockAssistantProvider(AIProviderInterface):
    name = "mock"
    is_demo = True
    model = "deterministic-template"

    def generate(self, request: AIRequest) -> AIReply:
        # The chat service composes the template text itself (it needs the full product dicts); the demo provider
        # simply returns it, which is exactly what makes demo mode deterministic.
        return AIReply(text=request.facts.get("template_text", ""))
