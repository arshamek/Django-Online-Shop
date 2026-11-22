"""
Step 6: deduplication / matching against canonical products (docs/architecture/DOMAIN_MODEL.md 4.1).

T1 (deterministic, auto-link): a GTIN shared with an already-linked listing, where at least one side's source
    treats its barcode field as a true GTIN, the brand agrees and exactly one canonical product is involved;
    or brand + manufacturer code.
T2 (strong, staff review): brand matches and several soft signals agree (title, category, colour, size, gender),
    or a GTIN match that is not deterministic.
T3 (weak): recorded as candidates only; never auto-links and never forces review.
Hard contradictions (gender, category root, disjoint trusted GTINs) veto soft matches. Image similarity is a
future extension and is intentionally absent. Nothing here is a title-only merge.
"""

import re
from dataclasses import dataclass, field

from django.conf import settings
from django.utils import timezone

from apps.catalog.models import Product, ProductStatus
from apps.common.text import normalize_search_text
from apps.product_sources.models import (
    CandidateStatus,
    ExternalProduct,
    ExternalProductVariant,
    MatchStatus,
    ProductMatchCandidate,
)

from .mapping import resolve_brand, resolve_category

W_TITLE, W_CATEGORY, W_COLOR, W_SIZE, W_GENDER = 0.45, 0.15, 0.10, 0.10, 0.10
GTIN_UNVERIFIED_SCORE = 0.70
POOL_LIMIT = 500
_STOP = {"the", "a", "an", "of", "and", "for", "with", "women", "woman", "men", "man", "kids", "unisex"}


@dataclass
class Candidate:
    product: Product
    score: float
    signals: dict = field(default_factory=dict)


@dataclass
class MatchOutcome:
    decision: str  # kept | auto_linked | needs_review | new
    product: Product | None = None
    confidence: float | None = None
    matched_by: str = ""
    reason: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    brand_id: int | None = None


def _tokens(text: str, brand_tokens: set[str]) -> set[str]:
    return {
        t for t in re.split(r"[^\w]+", normalize_search_text(text)) if t and t not in _STOP and t not in brand_tokens
    }


def _product_facts(product: Product) -> dict:
    listings = list(product.external_listings.all())
    variants = ExternalProductVariant.objects.filter(external_product__canonical_product=product)
    titles = [x.raw_title for x in listings] + list(product.translations.values_list("name", flat=True))
    return {
        "titles": titles,
        "genders": {x.gender for x in listings if x.gender},
        "colors": {v.color_key for v in variants if v.color_key},
        "sizes": {v.size_key for v in variants if v.size_key},
        "gtins": {v.gtin for v in variants if v.gtin and v.gtin_trusted},
    }


def _score(ext: ExternalProduct, ext_variants, ext_category, brand_tokens, product: Product) -> Candidate | None:
    facts = _product_facts(product)
    signals: dict = {}
    if (
        ext.gender
        and facts["genders"]
        and ext.gender not in facts["genders"]
        and "unisex" not in (facts["genders"] | {ext.gender})
    ):
        return None  # veto: contradicting gender
    if (
        ext_category is not None
        and product.category_id
        and ext_category.path.split("/")[1:2] != product.category.path.split("/")[1:2]
    ):
        return None  # veto: different category root
    mine = {v.gtin for v in ext_variants if v.gtin and v.gtin_trusted}
    if mine and facts["gtins"] and not (mine & facts["gtins"]):
        return None  # veto: both sides carry trusted GTINs and none agree
    mine_tokens = _tokens(ext.raw_title, brand_tokens)
    best = 0.0
    for title in facts["titles"]:
        other = _tokens(title, brand_tokens)
        if mine_tokens and other:
            best = max(best, len(mine_tokens & other) / len(mine_tokens | other))
    score = W_TITLE * best
    signals["title_similarity"] = round(best, 3)
    if ext_category is not None and ext_category.pk == product.category_id:
        score += W_CATEGORY
        signals["same_category"] = True
    my_colors, my_sizes = (
        {v.color_key for v in ext_variants if v.color_key},
        {v.size_key for v in ext_variants if v.size_key},
    )
    if my_colors & facts["colors"]:
        score += W_COLOR
        signals["shared_colors"] = sorted(my_colors & facts["colors"])
    if my_sizes & facts["sizes"]:
        score += W_SIZE
        signals["shared_sizes"] = sorted(my_sizes & facts["sizes"])
    if ext.gender and ext.gender in facts["genders"]:
        score += W_GENDER
        signals["same_gender"] = True
    return Candidate(product, round(min(score, 0.9), 3), signals)


def match(ext: ExternalProduct) -> MatchOutcome:
    if ext.canonical_product_id and ext.match_status in (MatchStatus.AUTO_MATCHED, MatchStatus.MANUALLY_MATCHED):
        return MatchOutcome("kept", ext.canonical_product, float(ext.match_confidence or 1), ext.matched_by)
    brand = resolve_brand(ext.source, ext.raw_brand)
    if brand is None:
        return MatchOutcome("needs_review", reason="brand_unresolved")
    out = MatchOutcome("new", brand_id=brand.pk)
    ext_variants = list(ext.variants.filter(is_active=True))

    # ---- T1: GTIN shared with a linked listing of another source row
    gtins = {v.gtin for v in ext_variants if v.gtin}
    hits: dict[int, dict] = {}
    if gtins:
        others = (
            ExternalProductVariant.objects.filter(gtin__in=gtins, external_product__canonical_product__isnull=False)
            .exclude(external_product=ext)
            .select_related("external_product__canonical_product")
        )
        mine = {v.gtin: v for v in ext_variants if v.gtin}
        for o in others:
            product = o.external_product.canonical_product
            entry = hits.setdefault(product.pk, {"product": product, "trusted": False, "n": 0})
            entry["trusted"] |= bool(mine[o.gtin].gtin_trusted or o.gtin_trusted)
            entry["n"] += 1
    if hits:
        if len(hits) == 1:
            entry = next(iter(hits.values()))
            if entry["trusted"] and entry["product"].brand_id == brand.pk:
                return MatchOutcome("auto_linked", entry["product"], 0.99, "rule:T1-gtin", brand_id=brand.pk)
            reason = "gtin_brand_conflict" if entry["product"].brand_id != brand.pk else "gtin_unverified"
            score = GTIN_UNVERIFIED_SCORE if reason == "gtin_unverified" else 0.5
            out.candidates.append(Candidate(entry["product"], score, {"shared_gtin": True, "reason": reason}))
            out.decision, out.reason = "needs_review", reason
        else:
            out.candidates += [Candidate(h["product"], 0.8, {"shared_gtin": True}) for h in hits.values()]
            out.decision, out.reason = "needs_review", "ambiguous_gtin"
        return out

    # ---- T1: brand + manufacturer code
    if ext.manufacturer_code:
        code = ext.manufacturer_code.upper()
        ids = set(
            ExternalProduct.objects.filter(
                brand_key=ext.brand_key, manufacturer_code=code, canonical_product__isnull=False
            )
            .exclude(pk=ext.pk)
            .values_list("canonical_product_id", flat=True)
        ) | set(Product.objects.filter(brand=brand, manufacturer_model_code__iexact=code).values_list("pk", flat=True))
        products = list(Product.objects.filter(pk__in=ids, brand=brand))
        if len(products) == 1:
            return MatchOutcome("auto_linked", products[0], 0.97, "rule:T1-mpn", brand_id=brand.pk)
        if len(products) > 1:
            out.candidates += [Candidate(p, 0.8, {"same_manufacturer_code": True}) for p in products]
            out.decision, out.reason = "needs_review", "ambiguous_manufacturer_code"
            return out

    # ---- T2 / T3: soft signals within the same brand
    ext_category = resolve_category(ext)
    brand_tokens = set(re.split(r"[^\w]+", normalize_search_text(brand.name)))
    pool = (
        Product.objects.filter(brand=brand)
        .exclude(status=ProductStatus.ARCHIVED)
        .select_related("category")[:POOL_LIMIT]
    )
    scored = [c for c in (_score(ext, ext_variants, ext_category, brand_tokens, p) for p in pool) if c]
    review_at, weak_at = settings.IMPORTS_MATCH_REVIEW_THRESHOLD, settings.IMPORTS_MATCH_WEAK_THRESHOLD
    out.candidates = sorted((c for c in scored if c.score >= weak_at), key=lambda c: -c.score)[:5]
    if any(c.score >= review_at for c in out.candidates):
        out.decision, out.reason = "needs_review", "match_candidates"
    elif settings.IMPORTS_UNMATCHED_POLICY == "review":
        out.decision, out.reason = "needs_review", "unmatched_policy_review"
    return out


def persist_candidates(ext: ExternalProduct, candidates: list[Candidate]) -> None:
    for c in candidates:
        obj, created = ProductMatchCandidate.objects.get_or_create(
            external_product=ext, product=c.product, defaults={"score": c.score, "signals": c.signals}
        )
        if not created and obj.status == CandidateStatus.PENDING:
            obj.score, obj.signals = c.score, c.signals
            obj.save(update_fields=["score", "signals"])


def apply_link(ext: ExternalProduct, product: Product, *, status: str, matched_by: str, confidence: float) -> None:
    ext.canonical_product = product
    ext.match_status = status
    ext.matched_by = matched_by[:60]
    ext.match_confidence = round(confidence, 3)
    ext.matched_at = timezone.now()
    ext.save(
        update_fields=[
            "canonical_product",
            "match_status",
            "matched_by",
            "match_confidence",
            "matched_at",
            "updated_at",
        ]
    )
