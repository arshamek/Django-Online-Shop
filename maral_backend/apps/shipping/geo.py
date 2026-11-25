"""Region / text normalisation for Persian and international addresses."""

import re

from apps.common.text import normalize_search_text

# (canonical key, Persian, Latin spellings). Reference geography only; no prices or policies are implied.
IRAN_PROVINCES = [
    ("alborz", "البرز", ["alborz"]),
    ("ardabil", "اردبیل", ["ardabil"]),
    ("bushehr", "بوشهر", ["bushehr", "bushehr"]),
    (
        "chaharmahal-bakhtiari",
        "چهارمحال و بختیاری",
        ["chaharmahal and bakhtiari", "chaharmahal & bakhtiari", "chaharmahal-bakhtiari"],
    ),
    ("east-azerbaijan", "آذربایجان شرقی", ["east azerbaijan", "azarbaijan sharqi"]),
    ("fars", "فارس", ["fars"]),
    ("gilan", "گیلان", ["gilan", "guilan"]),
    ("golestan", "گلستان", ["golestan"]),
    ("hamadan", "همدان", ["hamadan", "hamedan"]),
    ("hormozgan", "هرمزگان", ["hormozgan"]),
    ("ilam", "ایلام", ["ilam"]),
    ("isfahan", "اصفهان", ["isfahan", "esfahan"]),
    ("kerman", "کرمان", ["kerman"]),
    ("kermanshah", "کرمانشاه", ["kermanshah"]),
    ("razavi-khorasan", "خراسان رضوی", ["razavi khorasan", "khorasan razavi"]),
    ("north-khorasan", "خراسان شمالی", ["north khorasan", "khorasan shomali"]),
    ("south-khorasan", "خراسان جنوبی", ["south khorasan", "khorasan jonubi"]),
    ("khuzestan", "خوزستان", ["khuzestan", "khuzistan"]),
    (
        "kohgiluyeh-boyer-ahmad",
        "کهگیلویه و بویراحمد",
        ["kohgiluyeh and boyer-ahmad", "kohgiluyeh & boyer-ahmad", "kohgiluyeh va boyerahmad"],
    ),
    ("kurdistan", "کردستان", ["kurdistan", "kordestan"]),
    ("lorestan", "لرستان", ["lorestan"]),
    ("markazi", "مرکزی", ["markazi", "central"]),
    ("mazandaran", "مازندران", ["mazandaran"]),
    ("qazvin", "قزوین", ["qazvin"]),
    ("qom", "قم", ["qom", "ghom"]),
    ("semnan", "سمنان", ["semnan"]),
    (
        "sistan-baluchestan",
        "سیستان و بلوچستان",
        ["sistan and baluchestan", "sistan & baluchestan", "sistan va baluchestan"],
    ),
    ("tehran", "تهران", ["tehran"]),
    ("west-azerbaijan", "آذربایجان غربی", ["west azerbaijan", "azarbaijan gharbi"]),
    ("yazd", "یزد", ["yazd"]),
    ("zanjan", "زنجان", ["zanjan"]),
]


def normalize_text(value) -> str:
    """Persian letter variants unified, digits to ASCII, ZWNJ to space, casefolded, whitespace collapsed."""
    return normalize_search_text(str(value or ""))


def _strip_prefix(text: str) -> str:
    return re.sub(r"^(استان|province of|province)\s+|\s+province$", "", text).strip()


_IRAN_LOOKUP: dict[str, str] = {}
for _key, _fa, _latin in IRAN_PROVINCES:
    for _alias in [_fa, _key.replace("-", " "), *_latin]:
        _IRAN_LOOKUP[normalize_text(_alias)] = _key
assert len(IRAN_PROVINCES) == 31


def normalize_region(country: str, value: str) -> str:
    """A comparable key: canonical slug for Iranian provinces, normalised text elsewhere. '' for empty input."""
    text = _strip_prefix(normalize_text(value))
    if not text:
        return ""
    if (country or "").upper() == "IR":
        return _IRAN_LOOKUP.get(text, text)
    return text
