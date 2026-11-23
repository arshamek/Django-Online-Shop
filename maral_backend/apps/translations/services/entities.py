"""
What can be translated, where the source text is, where the result goes, and which targets are protected.

Translatable: product text (+ its free-text specifications), categories, attribute names, attribute values (colours ...).
NOT translatable: customer reviews. There is deliberately no adapter for them; see `assert_translatable`.
"""

import hashlib
import json
from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.brands.models import BrandAlias
from apps.catalog.models import (
    AttributeOption,
    AttributeOptionTranslation,
    Product,
    ProductAttribute,
    ProductAttributeTranslation,
    ProductAttributeValue,
    ProductTranslation,
    TranslationOrigin,
    TranslationStatus,
)
from apps.categories.models import Category, CategoryTranslation
from apps.common.exceptions import BusinessRuleViolation

from ..models import EntityType, ProtectedTerm, TranslationRecord

PRODUCT_FIELDS = ("name", "short_description", "description", "seo_title", "seo_description")
CONTEXT = {
    "name": "product title",
    "short_description": "short product description",
    "description": "product description",
    "seo_title": "page title",
    "seo_description": "meta description",
}
UNTRANSLATABLE = {"review", "customer_review", "reviews"}


def assert_translatable(entity_type: str) -> None:
    if entity_type in UNTRANSLATABLE:
        raise BusinessRuleViolation(
            "Customer reviews are not translated: that needs permission to process and republish them.",
            code="reviews_not_translatable",
        )
    if entity_type not in EntityType.values:
        raise BusinessRuleViolation(f"Unknown translatable entity: {entity_type}.", code="unknown_entity_type")


@dataclass
class State:
    origin: str
    status: str
    source_hash: str


@dataclass
class Loaded:
    source_language: str
    units: dict[str, str]
    groups: dict[str, list[str]]
    protected: list[str]
    source_hash: str
    context: dict[str, str] = field(default_factory=dict)


def _hash(units: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(units, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _extra_terms() -> list[str]:
    return list(ProtectedTerm.objects.filter(is_active=True).values_list("term", flat=True))


def _pick_source(rows, preferred="fa"):
    by_lang = {r.language: r for r in rows}
    if preferred in by_lang:
        return by_lang[preferred]
    return rows[0] if rows else None


def _record_state(entity_type, entity_id, language, row_exists: bool) -> State | None:
    if not row_exists:
        return None
    rec = TranslationRecord.objects.filter(entity_type=entity_type, entity_id=entity_id, language=language).first()
    if rec is None:  # a row nobody tracked = entered by staff before Phase 6: protected like human text
        return State(TranslationOrigin.HUMAN, TranslationStatus.HUMAN, "")
    return State(rec.origin, rec.status, rec.source_text_hash)


def _save_record(
    entity_type,
    entity_id,
    language,
    *,
    origin,
    status,
    source_hash,
    provider="",
    model="",
    reviewer=None,
    target_object_id=None,
):
    TranslationRecord.objects.update_or_create(
        entity_type=entity_type,
        entity_id=entity_id,
        language=language,
        defaults={
            "origin": origin,
            "status": status,
            "source_text_hash": source_hash,
            "provider": provider,
            "model": model,
            "reviewed_by": reviewer,
            "reviewed_at": timezone.now() if reviewer else None,
            "target_object_id": target_object_id,
        },
    )


# ---------------------------------------------------------------------------------------------- product
class ProductAdapter:
    entity_type = EntityType.PRODUCT

    def load(self, entity_id: int) -> Loaded | None:
        product = Product.objects.select_related("brand").filter(pk=entity_id).first()
        if product is None:
            return None
        source = ProductTranslation.objects.filter(product=product, language=product.source_language).first()
        if source is None:
            return None
        units, groups, context = {}, {"row": []}, {}
        for name in PRODUCT_FIELDS:
            value = getattr(source, name)
            if value and value.strip():
                units[name] = value
                groups["row"].append(name)
                context[name] = CONTEXT[name]
        for spec in ProductAttributeValue.objects.filter(product=product, language=product.source_language).exclude(
            value_text=""
        ):
            key = f"spec:{spec.pk}"
            units[key], groups[key], context[key] = spec.value_text, [key], "product specification value"
        if "name" not in units:
            return None
        brand_names = (
            {product.brand.name, *product.brand.translations.values_list("name_local", flat=True)}
            if product.brand_id
            else set()
        )
        if product.brand_id:
            brand_names |= set(BrandAlias.objects.filter(brand=product.brand).values_list("alias", flat=True))
        terms = [
            *brand_names,
            product.manufacturer_model_code,
            *product.variants.values_list("sku", flat=True),
            *_extra_terms(),
        ]
        return Loaded(product.source_language, units, groups, [t for t in terms if t], _hash(units), context)

    def states(self, entity_id, language) -> dict[str, State | None]:
        out: dict[str, State | None] = {}
        row = ProductTranslation.objects.filter(product_id=entity_id, language=language).first()
        out["row"] = State(row.origin, row.status, row.source_text_hash) if row else None
        for spec in (
            ProductAttributeValue.objects.filter(product_id=entity_id).exclude(value_text="").exclude(language=language)
        ):
            rec = TranslationRecord.objects.filter(
                entity_type="spec_value", entity_id=spec.pk, language=language
            ).first()
            sibling = (
                rec and rec.target_object_id and ProductAttributeValue.objects.filter(pk=rec.target_object_id).exists()
            )
            out[f"spec:{spec.pk}"] = State(rec.origin, rec.status, rec.source_text_hash) if sibling else None
        return out

    @transaction.atomic
    def write(
        self, entity_id, language, group, texts, *, origin, status, source_hash, provider="", model="", reviewer=None
    ):
        if group == "row":
            row, _ = ProductTranslation.objects.get_or_create(
                product_id=entity_id, language=language, defaults={"name": texts["name"]}
            )
            for name in PRODUCT_FIELDS:
                setattr(row, name, texts.get(name, "") if name != "name" else texts["name"])
            row.origin, row.status, row.source_text_hash, row.provider, row.model = (
                origin,
                status,
                source_hash,
                provider,
                model,
            )
            row.reviewed_by, row.reviewed_at = reviewer, timezone.now() if reviewer else None
            row.save()
            return
        spec_id = int(group.split(":")[1])
        source = ProductAttributeValue.objects.get(pk=spec_id, product_id=entity_id)
        rec = TranslationRecord.objects.filter(entity_type="spec_value", entity_id=spec_id, language=language).first()
        sibling = (
            ProductAttributeValue.objects.filter(pk=rec.target_object_id).first()
            if rec and rec.target_object_id
            else None
        )
        if sibling is None:
            sibling = ProductAttributeValue(
                product_id=entity_id, attribute=source.attribute, language=language, sort_order=source.sort_order
            )
        sibling.value_text = texts[group][:500]
        sibling.save()
        _save_record(
            "spec_value",
            spec_id,
            language,
            origin=origin,
            status=status,
            source_hash=source_hash,
            provider=provider,
            model=model,
            reviewer=reviewer,
            target_object_id=sibling.pk,
        )

    def set_status(self, entity_id, language, group, status, reviewer=None):
        if group == "row":
            ProductTranslation.objects.filter(product_id=entity_id, language=language).update(
                status=status, reviewed_by=reviewer, reviewed_at=timezone.now() if reviewer else None
            )
        else:
            TranslationRecord.objects.filter(
                entity_type="spec_value", entity_id=int(group.split(":")[1]), language=language
            ).update(status=status, reviewed_by=reviewer, reviewed_at=timezone.now() if reviewer else None)


# ------------------------------------------------------------------------------ category / attribute / option
class _SimpleAdapter:
    """Owners with one translation row per language and a `name` (+ optional description)."""

    entity_type = ""
    record_type = ""
    fields: tuple[str, ...] = ("name",)

    def _rows(self, entity_id):
        raise NotImplementedError

    def _create(self, entity_id, language, texts):
        raise NotImplementedError

    def load(self, entity_id):
        rows = list(self._rows(entity_id))
        source = _pick_source(rows)
        if source is None or not source.name.strip():
            return None
        units = {f: getattr(source, f) for f in self.fields if getattr(source, f, "").strip()}
        return Loaded(
            source.language,
            units,
            {"row": list(units)},
            _extra_terms(),
            _hash(units),
            {f: f"{self.entity_type} {f}" for f in units},
        )

    def states(self, entity_id, language):
        exists = any(r.language == language for r in self._rows(entity_id))
        return {"row": _record_state(self.record_type, entity_id, language, exists)}

    @transaction.atomic
    def write(
        self, entity_id, language, group, texts, *, origin, status, source_hash, provider="", model="", reviewer=None
    ):
        row = next((r for r in self._rows(entity_id) if r.language == language), None)
        if row is None:
            row = self._create(entity_id, language, texts)
        for f in self.fields:
            if f in texts:
                setattr(row, f, texts[f])
        row.save()
        _save_record(
            self.record_type,
            entity_id,
            language,
            origin=origin,
            status=status,
            source_hash=source_hash,
            provider=provider,
            model=model,
            reviewer=reviewer,
        )

    def set_status(self, entity_id, language, group, status, reviewer=None):
        TranslationRecord.objects.filter(entity_type=self.record_type, entity_id=entity_id, language=language).update(
            status=status, reviewed_by=reviewer, reviewed_at=timezone.now() if reviewer else None
        )


class CategoryAdapter(_SimpleAdapter):
    entity_type = EntityType.CATEGORY
    record_type = "category"
    fields = ("name", "description")

    def _rows(self, entity_id):
        return CategoryTranslation.objects.filter(category_id=entity_id)

    def _create(self, entity_id, language, texts):
        return CategoryTranslation(category=Category.objects.get(pk=entity_id), language=language, name=texts["name"])


class AttributeAdapter(_SimpleAdapter):
    entity_type = EntityType.ATTRIBUTE
    record_type = "attribute"

    def _rows(self, entity_id):
        return ProductAttributeTranslation.objects.filter(attribute_id=entity_id)

    def _create(self, entity_id, language, texts):
        return ProductAttributeTranslation(
            attribute=ProductAttribute.objects.get(pk=entity_id), language=language, name=texts["name"]
        )


class OptionAdapter(_SimpleAdapter):
    entity_type = EntityType.OPTION
    record_type = "option"

    def _rows(self, entity_id):
        return AttributeOptionTranslation.objects.filter(option_id=entity_id)

    def _create(self, entity_id, language, texts):
        return AttributeOptionTranslation(
            option=AttributeOption.objects.get(pk=entity_id), language=language, name=texts["name"]
        )


ADAPTERS = {a.entity_type: a for a in (ProductAdapter(), CategoryAdapter(), AttributeAdapter(), OptionAdapter())}


def adapter_for(entity_type: str):
    assert_translatable(entity_type)
    return ADAPTERS[entity_type]
