"""Content languages (fa is the primary language; see docs/architecture/TECHNICAL_DECISIONS.md)."""

LANGUAGE_CHOICES = [("fa", "Persian"), ("en", "English"), ("ar", "Arabic"), ("tr", "Turkish")]
SUPPORTED_LANGUAGES = tuple(code for code, _ in LANGUAGE_CHOICES)
DEFAULT_LANGUAGE = "fa"
# requested -> fa -> en -> (the product's own source language) -> anything
FALLBACK_LANGUAGES = ("fa", "en")


def language_chain(requested: str | None, *extra: str) -> list[str]:
    chain: list[str] = []
    for code in (requested, *FALLBACK_LANGUAGES, *extra):
        if code and code in SUPPORTED_LANGUAGES and code not in chain:
            chain.append(code)
    return chain
