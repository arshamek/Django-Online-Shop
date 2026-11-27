"""
Untrusted-input handling and reply validation.

Customer messages, imported marketplace data and model output are all UNTRUSTED:
* `sanitize_untrusted` cleans a text field and reports whether it looks like a prompt injection. A flagged field is
  never forwarded to the model.
* The model has no tools and no write access: it receives pre-computed facts and returns text only.
* `validate_reply` rejects model text that states prices, URLs, delivery times or availability the backend did not
  provide. A rejected reply is replaced by the deterministic template.
"""

import json
import re
import unicodedata

from .textnorm import fold

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b\u200d\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff]")
_TAGS = re.compile(r"<[^>]{0,200}>")
_FENCE = re.compile(r"(```|~~~|<<<|>>>|\|\|\||\{\{|\}\}|\[\[|\]\])")
_ROLE_LINE = re.compile(r"(?im)^\s*(system|assistant|user|human|developer|ai)\s*[:：>]")
_SPACES = re.compile(r"[ \t\r\f\v]+")

_INJECTION = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts?|rules)",
    r"disregard (all |any )?(the )?(previous|prior|above|system)",
    r"forget (all |your )?(previous|prior|earlier)? ?(instructions|rules)",
    r"(reveal|show|print|repeat) (me )?(your |the )?(system|hidden|initial) (prompt|instructions|message)",
    r"you are now\b",
    r"act as (an? )?(?!shopping)",
    r"new (instructions|rules|persona)",
    r"developer mode",
    r"jailbreak",
    r"override (the )?(system|safety|price|pricing)",
    r"set (the )?price to",
    r"mark (this|it) (as )?(in stock|available|free)",
    r"</?(system|assistant|instructions?|tool|function)[^>]*>",
    r"<\|[a-z_]+\|>",
    r"دستور(ات|الع?مل)? (های )?(قبلی|بالا|پیشین) را? ?(نادیده|فراموش)",
    r"(نادیده|فراموش) ?(بگیر|کن)",
    r"از این پس (تو|شما)",
    r"پرامپت سیستم",
    r"دستورالعمل(‌| )?های (سیستم|داخلی)",
    r"قیمت را .{0,20}(صفر|رایگان)",
    r"önceki (talimat|komut)",
    r"sistem (istemi|talimat)",
    r"تجاهل (جميع |كل )?(التعليمات|الاوامر)",
    r"تعليمات النظام",
]
_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in _INJECTION]


def looks_like_injection(text: str) -> bool:
    if not text:
        return False
    plain = unicodedata.normalize("NFKC", text)
    folded = fold(text)
    return any(r.search(plain) or r.search(folded) for r in _INJECTION_RE)


def clean_text(text: str | None, max_len: int) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = _CONTROL.sub("", text)
    text = _TAGS.sub(" ", text)
    text = _FENCE.sub(" ", text)
    text = _ROLE_LINE.sub("", text)
    text = _SPACES.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_len]


def sanitize_untrusted(text: str | None, max_len: int = 200) -> tuple[str, bool]:
    """(clean text, flagged). Flagged text must not be shown to the model; callers drop it."""
    cleaned = clean_text(text, max_len)
    return cleaned, looks_like_injection(str(text or ""))


# ------------------------------------------------------------------------------- untrusted text inside the facts
WITHHELD = "(text withheld)"
_ROLE_ANYWHERE = re.compile(r"\b(system|assistant|user|human|developer|ai|tool|function)\s*[:：>]", re.IGNORECASE)
_SIZE_OK = re.compile(r"^[0-9a-z][0-9a-z./-]{0,7}$")


def safe_fact_text(value, max_len: int = 80) -> str:
    """
    One marketplace-, catalogue- or customer-originated string, made safe to put in the model's context as DATA:
    sanitised (markup, control/bidi characters, fences, role markers), forced onto a single line, role labels in the
    middle of the text neutralised, truncated, and replaced by WITHHELD when it looks like a prompt injection.
    Idempotent. Callers still serialise it with `safe_json` and keep it in an `untrusted_text` group.
    """
    if value is None:
        return ""
    cleaned, flagged = sanitize_untrusted(str(value), max_len * 2)
    if flagged:
        return WITHHELD
    single = re.sub(r"\s+", " ", cleaned).strip()
    single = _ROLE_ANYWHERE.sub(lambda m: m.group(1) + " -", single)
    return single[:max_len].strip()


def safe_size_token(value) -> str:
    """
    A size that may be used as an authoritative token (in-stock size checks). Stricter than sanitising: the raw value
    (digits/letters folded) must ALREADY be a short allow-listed token such as 42, 42.5, xl or 8/9; anything that would
    first need cleaning (markup, spaces, role labels, ...) is rejected, not repaired. Returns '' when rejected.
    """
    token = fold(str(value if value is not None else "")).strip()
    return token if _SIZE_OK.match(token) else ""


def safe_json(obj) -> str:
    """
    Compact JSON for the model context in which `<`, `>`, `&` and the Unicode line separators are escaped
    (\\u003c ...). The text stays valid JSON and readable to the model, but a string can never contain a literal
    delimiter tag such as `</facts>` or start a new line of "instructions".
    """
    out = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    for char, escaped in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        out = out.replace(char, escaped)
    return out


# ------------------------------------------------------------------------------------------- reply validation
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_DIGITS = re.compile(r"\d[\d.,٬٫ ]*\d|\d")
_DELIVERY = re.compile(r"\d+\s*(?:-|تا|to)?\s*\d*\s*(?:روز|days?|gün|يوم|ايام|أيام|business)", re.IGNORECASE)
_AVAILABLE_CLAIM = [
    r"(?<!نا)موجود(?: است| می‌?باشد|ه| هستند|ن)\b",
    r"\bin stock\b",
    r"\bis available\b",
    r"\bare available\b",
    r"\bstokta (?:var|mevcut)\b",
    r"\bmevcuttur\b",
    r"(?<!غير )متوفر[ةه]?\b",
]
_AVAILABLE_CLAIM_RE = [re.compile(p, re.IGNORECASE) for p in _AVAILABLE_CLAIM]
_NEGATED = re.compile(r"(?:not|n't|isn't|aren't|نا|نیست|değil|degil|غير|ليس)\s*$", re.IGNORECASE)


def _numbers(text: str) -> set:
    out = set()
    for match in _DIGITS.finditer(fold(text)):
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) >= 4:
            out.add(digits)
    return out


_SENTENCE_SPLIT = re.compile(r"[.!?؟\n]+")
_SIZE_TOKEN = re.compile(r"\b(\d{2})\b")


def _claims_availability(sentence: str) -> bool:
    for pattern in _AVAILABLE_CLAIM_RE:
        for match in pattern.finditer(sentence):
            if not _NEGATED.search(sentence[max(0, match.start() - 12) : match.start()]):
                return True
    return False


def validate_reply(
    text: str,
    *,
    allowed_numbers: set,
    allowed_urls: set,
    all_in_stock: bool,
    in_stock_sizes: list | None = None,
) -> tuple[bool, str]:
    """
    (ok, reason). `allowed_numbers` are digit strings (>= 4 digits) the backend actually provided.

    Availability statements in free text cannot be attributed to a single product, so they are only accepted when
    EVERY product shown is IN_STOCK, and a size mentioned in such a sentence must be in stock for every product
    (`in_stock_sizes` is one set per product). Otherwise the structured per-product `availability` of the cards (and
    the backend's own template) is the only source of availability statements.
    """
    if not text or not text.strip():
        return False, "empty"
    if len(text) > 2500:
        return False, "too_long"
    if looks_like_injection(text) or _ROLE_LINE.search(text):
        return False, "injection_echo"
    for url in _URL.findall(text):
        if url.rstrip(".,)") not in allowed_urls:
            return False, "unknown_url"
    unknown = _numbers(text) - {re.sub(r"\D", "", n) for n in allowed_numbers}
    if unknown:
        return False, "unknown_number"
    if _DELIVERY.search(fold(text)) or _DELIVERY.search(text):
        return False, "delivery_claim"
    for sentence in _SENTENCE_SPLIT.split(text):
        if not _claims_availability(sentence):
            continue
        if not all_in_stock:
            return False, "availability_claim"
        sizes_per_product = in_stock_sizes or []
        for size in _SIZE_TOKEN.findall(fold(sentence)):
            if any(size not in sizes for sizes in sizes_per_product):
                return False, "availability_size_claim"
    return True, ""
