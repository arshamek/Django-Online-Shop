"""AIProviderInterface: the only thing the chat service knows about a language model."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class AIProviderError(Exception):
    """Safe to show/log: carries a short code, never credentials, URLs or response bodies."""

    def __init__(self, code: str, message: str = "", transient: bool = True):
        super().__init__(message or code)
        self.code = code
        self.transient = transient


@dataclass
class AIRequest:
    language: str
    system_prompt: str
    customer_message: str  # untrusted
    history: list = field(
        default_factory=list
    )  # [{"role": "user", "text": str}]: sanitised EARLIER CUSTOMER messages of THIS conversation (untrusted data)
    facts: dict = field(default_factory=dict)  # authoritative, computed by the backend
    timeout: int = 20


@dataclass
class AIReply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class AIProviderInterface(ABC):
    name = "base"
    is_demo = False
    model = ""

    @abstractmethod
    def generate(self, request: AIRequest) -> AIReply:
        """Phrase a reply from `request.facts`. Must not be relied on for any fact."""
