"""
Language detection for fa / en / ar / tr. Rule-based on purpose (deterministic, no model call): script first, then
distinguishing letters and function words. Returns (code, confident). Mixed Persian + English brand names stay Persian.
"""

import re

from .textnorm import fold, has_arabic_script, has_latin

_FA_LETTERS = re.compile("[پچژگ]")
_AR_LETTERS = re.compile("[ةيىكؤئإأ]")  # raw (pre-normalisation) Arabic-only forms
_FA_WORDS = {
    "می",
    "را",
    "است",
    "که",
    "این",
    "اون",
    "رو",
    "برای",
    "میخوام",
    "می‌خوام",
    "خوام",
    "باشه",
    "هست",
    "یه",
    "یک",
    "نیست",
    "ارزون",
    "تومن",
    "تومان",
    "کتونی",
    "کفش",
    "سایز",
    "لطفا",
    "ممنون",
    "چی",
    "چه",
    "کدوم",
    "قیمت",
}
_AR_WORDS = {
    "أريد",
    "اريد",
    "هل",
    "في",
    "من",
    "على",
    "عن",
    "إلى",
    "الى",
    "هذا",
    "هذه",
    "ماذا",
    "كم",
    "سعر",
    "حذاء",
    "لدي",
    "أرخص",
    "ارخص",
    "رجاء",
    "من فضلك",
    "متوفر",
    "مقاس",
    "ريال",
    "تومان",
    "بدي",
}
_TR_LETTERS = re.compile("[çğışöüÇĞİŞÖÜ]")
_TR_WORDS = {
    "bir",
    "istiyorum",
    "istiyorum.",
    "fiyat",
    "beden",
    "numara",
    "icin",
    "için",
    "var",
    "mi",
    "mı",
    "mu",
    "mü",
    "ucuz",
    "daha",
    "siyah",
    "beyaz",
    "renk",
    "ayakkabi",
    "ayakkabı",
    "altinda",
    "altında",
    "alti",
    "altı",
    "lutfen",
    "lütfen",
    "merhaba",
    "evet",
    "hayir",
    "ve",
    "ile",
    "toman",
    "milyon",
    "sadece",
    "karsilastir",
    "karşılaştır",
    "stokta",
    "kac",
    "kaç",
    "nedir",
    "göster",
    "goster",
    "bana",
    "ben",
}
_EN_WORDS = {
    "the",
    "i",
    "want",
    "need",
    "looking",
    "for",
    "a",
    "an",
    "show",
    "me",
    "cheaper",
    "under",
    "below",
    "size",
    "black",
    "white",
    "shoes",
    "sneakers",
    "price",
    "available",
    "compare",
    "please",
    "only",
    "and",
    "is",
    "are",
    "do",
    "you",
    "have",
    "with",
    "than",
    "find",
    "search",
    "too",
    "also",
    "which",
    "what",
    "how",
    "much",
}


def detect_language(text: str, fallback: str = "fa") -> tuple[str, bool]:
    raw = text or ""
    if not re.search(r"[^\W\d_]", raw):
        return fallback, False
    words = set(re.findall(r"[^\W\d_]+", raw.lower()))
    if has_arabic_script(raw):
        ar_score = len(_AR_LETTERS.findall(raw)) * 2 + len(words & _AR_WORDS) * 2
        fa_score = len(_FA_LETTERS.findall(raw)) * 3 + len(words & _FA_WORDS) * 2
        folded = set(fold(raw).split())
        fa_score += len(folded & {fold(w) for w in _FA_WORDS})
        if ar_score > fa_score:
            return "ar", True
        return "fa", True
    if not has_latin(raw):
        return fallback, False
    tr_score = len(_TR_LETTERS.findall(raw)) * 2 + len(words & _TR_WORDS)
    en_score = len(words & _EN_WORDS)
    if tr_score > en_score:
        return "tr", tr_score >= 2
    if en_score > 0:
        return "en", True
    return ("tr", False) if tr_score else ("en", False)
