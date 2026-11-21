"""
Text normalisation for search (docs/database/INDEX_STRATEGY.md: Persian search).

Arabic yeh/kaf -> Persian, ZWNJ -> space, Persian/Arabic-Indic digits -> ASCII, diacritics and tatweel
removed, Latin lower-cased, whitespace collapsed.
"""

import re
import unicodedata

_TRANSLATION = str.maketrans(
    {
        "ي": "ی",
        "ى": "ی",
        "ك": "ک",
        "ة": "ه",
        "ۀ": "ه",
        "أ": "ا",
        "إ": "ا",
        "ٱ": "ا",
        "\u200c": " ",  # ZWNJ
        "\u200d": "",  # ZWJ
        "\u200f": "",
        "\u200e": "",
        "ـ": "",  # tatweel
        "٫": ".",
        "٬": "",
        "،": " ",
        "؛": " ",
        **{chr(0x06F0 + i): str(i) for i in range(10)},  # Extended Arabic-Indic (Persian)
        **{chr(0x0660 + i): str(i) for i in range(10)},  # Arabic-Indic
    }
)
_DIACRITICS = re.compile("[\u064b-\u065f\u0670]")
_SPACES = re.compile(r"\s+")


def normalize_search_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).translate(_TRANSLATION)
    text = _DIACRITICS.sub("", text).casefold()
    return _SPACES.sub(" ", text).strip()
