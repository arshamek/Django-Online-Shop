"""
Query-side text folding for the assistant. Builds on `apps.common.text.normalize_search_text` (ی/ي, ک/ك, digits,
ZWNJ) and additionally folds Turkish letters so one vocabulary serves fa/en/ar/tr. Used for MATCHING only: stored
product titles, brand names, SKUs and model numbers are never modified.
"""

import re
import unicodedata

from apps.common.text import normalize_search_text

_TR = str.maketrans({"ı": "i", "ş": "s", "ğ": "g", "ç": "c", "ö": "o", "ü": "u", "â": "a", "î": "i", "û": "u"})
_COMBINING = re.compile("[\u0300-\u036f]")
_ARABIC_SCRIPT = re.compile("[\u0600-\u06ff\u0750-\u077f]")
_LATIN = re.compile("[a-zA-ZçğıöşüÇĞİÖŞÜ]")


def fold(text: str | None) -> str:
    base = normalize_search_text(text)
    base = unicodedata.normalize("NFKD", base)
    base = _COMBINING.sub("", base).translate(_TR)
    return re.sub(r"\s+", " ", base).strip()


def tokenize(text: str | None) -> list[str]:
    """Folded tokens with thousands separators removed and digit/letter boundaries split."""
    t = fold(text)
    t = re.sub(r"(\d)[,،](?=\d{3}(?!\d))", r"\1", t)
    t = re.sub(r"(?<=\d)\.(?=\d{3}(?!\d))", "", t)
    t = re.sub(r"(\d)([^\W\d_])", r"\1 \2", t)
    t = re.sub(r"([^\W\d_])(\d)", r"\1 \2", t)
    t = t.replace("-", " - ").replace("–", " - ")
    t = re.sub(r"[?؟!\"'«»()\[\]{}:;/\\|*#@]", " ", t)
    t = re.sub(r"(?<!\d)[.,]|[.,](?!\d)", " ", t)
    return t.split()


def has_arabic_script(text: str) -> bool:
    return bool(_ARABIC_SCRIPT.search(text or ""))


def has_latin(text: str) -> bool:
    return bool(_LATIN.search(text or ""))
