"""
Natural-language understanding for shopping requests. Deterministic and rule-based: it turns a Persian / English /
Arabic / Turkish message into a `ParsedRequest` (structured filters + intent). No model is involved, so the
extraction is testable and cannot hallucinate; the language model only phrases the final reply.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.cache import cache

from . import vocab
from .language import detect_language
from .numbers import Budget, parse_budget, parse_sizes
from .textnorm import fold, tokenize


@dataclass
class ParsedRequest:
    text: str
    language: str
    language_confident: bool
    intent: str = "search"  # search | compare | availability | select | greeting | unclear
    concept: str = ""
    uses: list = field(default_factory=list)
    brands: list = field(default_factory=list)  # Latin/canonical hints
    brand_ids: list = field(default_factory=list)
    keywords: list = field(default_factory=list)
    colors: list = field(default_factory=list)
    sizes: list = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    cheaper: bool = False
    only_sources: list = field(default_factory=list)
    add_sources: list = field(default_factory=list)
    ordinals: list = field(default_factory=list)
    has_new_search_terms: bool = False

    @property
    def min_irr(self) -> Decimal | None:
        return self.budget.min_irr

    @property
    def max_irr(self) -> Decimal | None:
        return self.budget.max_irr


def _phrase_positions(tokens: list, phrases) -> list:
    """[(start, end)] of every phrase occurrence (longest phrases first, no overlaps)."""
    found, used = [], set()
    for phrase in sorted(phrases, key=lambda p: -len(p.split())):
        words = phrase.split()
        n = len(words)
        for i in range(len(tokens) - n + 1):
            if tokens[i : i + n] == words and not used.intersection(range(i, i + n)):
                found.append((i, i + n))
                used.update(range(i, i + n))
    return sorted(found)


def _brand_index() -> dict:
    """folded brand text -> (brand_id, display name); from the catalogue's own names, local names and aliases."""
    cached = cache.get("ai:brand-index")
    if cached is not None:
        return cached
    from apps.brands.models import Brand, BrandAlias, BrandTranslation

    index: dict = {}
    for brand in Brand.objects.all().only("id", "name"):
        index[fold(brand.name)] = (brand.id, brand.name)
    for row in BrandTranslation.objects.select_related("brand").only("name_local", "brand__id", "brand__name"):
        if row.name_local:
            index[fold(row.name_local)] = (row.brand.id, row.brand.name)
    for row in BrandAlias.objects.select_related("brand").only("alias_normalised", "brand__id", "brand__name"):
        if row.alias_normalised:
            index[fold(row.alias_normalised)] = (row.brand.id, row.brand.name)
    cache.set("ai:brand-index", index, 120)
    return index


def parse_message(text: str, *, fallback_language: str = "fa", forced_language: str | None = None) -> ParsedRequest:
    detected, confident = detect_language(text, fallback_language)
    language = forced_language or detected
    req = ParsedRequest(text=text, language=language, language_confident=confident or bool(forced_language))
    tokens = tokenize(text)
    joined = " ".join(tokens)
    consumed: set = set()

    sizes, size_used = parse_sizes(tokens)
    req.sizes = sizes
    consumed |= size_used

    req.budget = parse_budget(tokens, exclude=size_used)
    consumed |= req.budget.consumed

    # sources
    for code, names in vocab.SOURCES.items():
        for start, end in _phrase_positions(tokens, names):
            consumed.update(range(start, end))
            before = tokens[max(0, start - 2) : start]
            after = tokens[end : end + 2]
            if any(w in vocab.SOURCE_ONLY for w in before) or any(w in vocab.SOURCE_ONLY for w in after):
                req.only_sources.append(code)
            elif any(w in vocab.SOURCE_ADD for w in after[:1]) or any(
                w in vocab.SOURCE_ADD for w in tokens[end : end + 3]
            ):
                req.add_sources.append(code)
            else:
                req.only_sources.append(code)
    req.only_sources = list(dict.fromkeys(req.only_sources))
    req.add_sources = [c for c in dict.fromkeys(req.add_sources) if c not in req.only_sources]

    # colours
    for code, names in vocab.COLORS.items():
        for start, end in _phrase_positions(tokens, names):
            consumed.update(range(start, end))
            req.colors.append(code)
    req.colors = list(dict.fromkeys(req.colors))

    # concept + use
    best = None
    for key, spec in vocab.CONCEPTS.items():
        for start, end in _phrase_positions(tokens, spec["triggers"]):
            if best is None or key == "sneakers":
                best = key
            consumed.update(range(start, end))
    req.concept = best or ""
    for use, names in vocab.USES.items():
        positions = _phrase_positions(tokens, names)
        if positions:
            req.uses.append(use)
            for start, end in positions:
                consumed.update(range(start, end))

    # brands: the catalogue's own vocabulary first, then transliterations
    index = _brand_index()
    brand_phrases = [p for p in index if p]
    for start, end in _phrase_positions(tokens, brand_phrases):
        phrase = " ".join(tokens[start:end])
        brand_id, name = index[phrase]
        consumed.update(range(start, end))
        if brand_id not in req.brand_ids:
            req.brand_ids.append(brand_id)
            req.brands.append(name)
    for start, end in _phrase_positions(tokens, vocab.BRAND_TRANSLITERATIONS):
        phrase = " ".join(tokens[start:end])
        consumed.update(range(start, end))
        latin = vocab.BRAND_TRANSLITERATIONS[phrase]
        if latin not in [b.lower() for b in req.brands]:
            req.brands.append(latin)

    # intents
    req.cheaper = any(re.search(pattern, joined) for pattern in vocab.CHEAPER)
    if req.cheaper:
        for i, tok in enumerate(tokens):
            if any(re.fullmatch(p.replace(r"\s?", ""), tok) for p in vocab.CHEAPER) or tok in ("ترشو", "تر"):
                consumed.add(i)
    for number, names in vocab.ORDINALS.items():
        if _phrase_positions(tokens, names):
            req.ordinals.append(number)
    for match in re.finditer(r"(?:#|گزینه|option|number|no|محصول|urun|رقم)\s?(\d)\b", fold(text)):
        req.ordinals.append(int(match.group(1)))
    req.ordinals = sorted(set(req.ordinals))
    wants_compare = bool(_phrase_positions(tokens, vocab.COMPARE))
    wants_select = bool(_phrase_positions(tokens, vocab.SELECT))
    asks_availability = bool(_phrase_positions(tokens, vocab.AVAILABILITY))
    greeting = bool(_phrase_positions(tokens, vocab.GREETING))

    leftovers = [
        t
        for i, t in enumerate(tokens)
        if i not in consumed
        and t not in vocab.STOPWORDS
        and not re.fullmatch(r"[\d.\-]+", t)
        and len(t) > 1
        and t
        not in {w for ph in (vocab.COMPARE + vocab.AVAILABILITY + vocab.SELECT + vocab.GREETING) for w in ph.split()}
        and t not in {w for names in vocab.ORDINALS.values() for ph in names for w in ph.split()}
        and t not in {"ایکس", "لارج", "size", "numara", "سایز", "beden", "شماره"}
    ]
    req.keywords = leftovers[:6]
    req.has_new_search_terms = bool(req.concept or req.brands or req.keywords)

    if wants_compare:
        req.intent = "compare"
    elif asks_availability and not req.has_new_search_terms:
        req.intent = "availability"
    elif asks_availability and req.sizes and not req.concept and not req.brands:
        req.intent = "availability"
    elif wants_select and not req.has_new_search_terms or (req.ordinals and wants_select):
        req.intent = "select"
    elif greeting and not (req.has_new_search_terms or req.budget.present or req.sizes or req.colors):
        req.intent = "greeting"
    elif not (
        req.has_new_search_terms
        or req.budget.present
        or req.sizes
        or req.colors
        or req.cheaper
        or req.only_sources
        or req.add_sources
        or req.ordinals
    ):
        req.intent = "unclear"
    else:
        req.intent = "search"
    if req.intent == "search" and req.ordinals and not req.has_new_search_terms and not req.cheaper:
        req.intent = "select"
    return req
