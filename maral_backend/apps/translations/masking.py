"""
Protect brand names, model numbers, product codes, measurements and numbers from translation.

Protected spans are replaced by opaque placeholders (⟦n⟧) before text leaves the server and restored afterwards. The
provider's answer is then verified: every placeholder exactly once, nothing invented, and the digit runs of the
restored text equal those of the source. Anything else is rejected - never saved.
"""

import re
from collections import Counter

from .errors import TranslationValidationError

OPEN, CLOSE = "⟦", "⟧"
_PLACEHOLDER = re.compile(r"⟦(\d+)⟧")
_DIGITS = "0-9\u06f0-\u06f9\u0660-\u0669"
_NUMBER = rf"[{_DIGITS}]+(?:[.,\u066b\u066c][{_DIGITS}]+)*"
_UNITS = r"(?:mm|cm|km|kg|mg|ml|oz|lb|lbs|inch|inches|gb|tb|mb|kb|mah|wh|ghz|mhz|hz|fps|mp|ml|m|g|l|w|v|a|in|°c|°f|°|%|\"|″|′)"
_MEASURE = re.compile(rf"(?<![\w]){_NUMBER}\s?{_UNITS}(?![A-Za-z])", re.IGNORECASE)
# a token that mixes letters and digits (SKU / model number / product code): XJ-900, A2345, 5G-X1
_CODE = re.compile(
    r"(?<![\w-])(?=[A-Za-z0-9./_-]*\d)(?=[A-Za-z0-9./_-]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9./_-]{1,}(?![\w-])"
)
_BARE_NUMBER = re.compile(_NUMBER)
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def protect(text: str, terms: list[str] | None = None) -> tuple[str, list[str]]:
    """Returns (masked text, originals by placeholder index)."""
    originals: list[str] = []

    def stash(match: re.Match) -> str:
        originals.append(match.group(0))
        return f"{OPEN}{len(originals) - 1}{CLOSE}"

    masked = text
    unique_terms = sorted({t.strip() for t in (terms or []) if t and t.strip()}, key=len, reverse=True)
    for term in unique_terms:
        masked = re.sub(rf"(?<![\w]){re.escape(term)}(?![\w])", stash, masked, flags=re.IGNORECASE)
    # later patterns must not re-match inside earlier placeholders
    for pattern in (_CODE, _MEASURE, _BARE_NUMBER):
        masked = _apply_outside_placeholders(pattern, masked, stash)
    return masked, originals


def _apply_outside_placeholders(pattern: re.Pattern, text: str, stash) -> str:
    out, last = [], 0
    for ph in _PLACEHOLDER.finditer(text):
        out.append(pattern.sub(stash, text[last : ph.start()]))
        out.append(ph.group(0))
        last = ph.end()
    out.append(pattern.sub(stash, text[last:]))
    return "".join(out)


def needs_translation(masked: str) -> bool:
    """False when nothing but placeholders / punctuation is left (e.g. a size '42' or 'XL-2000')."""
    return bool(_LETTER.search(_PLACEHOLDER.sub("", masked)))


def restore(translated: str, originals: list[str], masked_source: str) -> str:
    expected = Counter(_PLACEHOLDER.findall(masked_source))
    got = Counter(_PLACEHOLDER.findall(translated))
    if got != expected:
        missing, extra = expected - got, got - expected
        raise TranslationValidationError("placeholder_mismatch", f"missing={dict(missing)} extra={dict(extra)}")
    if OPEN in _PLACEHOLDER.sub("", translated) or CLOSE in _PLACEHOLDER.sub("", translated):
        raise TranslationValidationError("placeholder_damaged", "A placeholder was altered.")
    return _PLACEHOLDER.sub(lambda m: originals[int(m.group(1))], translated)


def digit_runs(text: str) -> Counter:
    return Counter(re.findall(rf"[{_DIGITS}]+", text))


def verify_numbers(source: str, restored: str) -> None:
    if digit_runs(source) != digit_runs(restored):
        raise TranslationValidationError("numbers_changed", "Numbers in the translation differ from the source.")
