"""
Response builders. They read only what `selectors.with_card_data` / detail prefetches loaded, so rendering a
page of N products issues the same number of queries for any N. Shapes follow docs/api/API_CONVENTIONS.md
(money objects with decimal strings) and carry the extra fields the existing React types need
(`fulfillment` stock|order, `is_new`, sizes, colours).
"""

from django.conf import settings

from apps.brands.models import Brand
from apps.categories.models import Category

from .cache import cached
from .context import CatalogContext, money, plain_number
from .models import (
    AttributeOption,
    AvailabilityStatus,
    DeliveryEstimateRule,
    FulfillmentSummary,
    Product,
    TranslationStatus,
)


# ----------------------------------------------------------------------------- helpers
def file_url(ctx: CatalogContext, field) -> str | None:
    if not field:
        return None
    url = field.url
    return url if url.startswith(("http://", "https://")) else ctx.request.build_absolute_uri(url)


def _usable(translation) -> bool:
    if settings.CATALOG_SHOW_MACHINE_TRANSLATIONS:
        return True
    # Only product translations carry an origin/status; brand, category and option names are always usable.
    return not (getattr(translation, "origin", None) == "machine" and translation.status != TranslationStatus.APPROVED)


def pick(translations, chain, source_language: str = ""):
    """First translation in the fallback chain (requested -> fa -> en -> source language -> anything)."""
    usable = [t for t in translations if _usable(t)]
    by_language = {t.language: t for t in usable}
    for code in (*chain, source_language):
        if code and code in by_language:
            return by_language[code]
    return usable[0] if usable else None


def brand_name(brand: Brand, ctx) -> str:
    """A brand shows its canonical (Latin) name unless a translation exists for the *requested* language."""
    for t in brand.translations.all():
        if t.language == ctx.lang:
            return t.name_local
    return brand.name


def category_name(category: Category, ctx) -> str:
    t = pick(list(category.translations.all()), ctx.chain)
    return t.name if t else category.slug


def option_name(option: AttributeOption, ctx) -> str:
    t = pick(list(option.translations.all()), ctx.chain)
    return t.name if t else option.code


def brand_ref(brand: Brand, ctx) -> dict:
    return {"id": str(brand.public_id), "slug": brand.slug, "name": brand_name(brand, ctx)}


def category_ref(category: Category, ctx) -> dict:
    return {"id": str(category.public_id), "slug": category.slug, "name": category_name(category, ctx)}


def source_ref(source) -> dict | None:
    if source is None:
        return None
    return {
        "code": source.code,
        "name": source.name,
        "country_code": source.country_code or None,
        "is_direct_inventory": source.is_direct_inventory,
    }


def image_ref(image, ctx, fallback_alt: str) -> dict:
    return {
        "id": str(image.public_id),
        "url": file_url(ctx, image.image),
        "thumbnail_url": file_url(ctx, image.thumbnail) or file_url(ctx, image.image),
        "alt": image.alt or fallback_alt,
        "width": image.width,
        "height": image.height,
        "attribution": image.attribution or None,
    }


def is_sellable(variant) -> bool:
    availability = getattr(variant, "availability", None)
    return bool(variant.is_active and availability and availability.status == AvailabilityStatus.IN_STOCK)


def price_block(ctx, *, original_amount, original_currency, list_irr, final_irr, discount, price_range=None) -> dict:
    from .context import amount_string

    block = {
        "original": {"amount": amount_string(original_amount), "currency": original_currency},
        "list": money(list_irr, ctx.currency),
        "final": money(final_irr, ctx.currency),
        "currency": ctx.currency,
        "discount_percent": plain_number(discount),
    }
    if price_range is not None:
        lo, hi = price_range
        block["range"] = None if lo == hi else {"min": money(lo, ctx.currency), "max": money(hi, ctx.currency)}
    return block


def _axis(variants, ctx, code, with_swatch=False) -> list:
    seen: dict = {}
    for variant in variants:
        for link in variant.options.all():
            if link.attribute.code != code:
                continue
            entry = seen.setdefault(
                link.option.code,
                {
                    "code": link.option.code,
                    "name": option_name(link.option, ctx),
                    "available": False,
                    "_order": (link.option.sort_order, link.option.pk),
                    **({"value": link.option.swatch or None} if with_swatch else {}),
                },
            )
            entry["available"] = entry["available"] or is_sellable(variant)
    ordered = sorted(seen.values(), key=lambda e: e["_order"])
    for entry in ordered:
        entry.pop("_order")
    return ordered


def _title(product: Product, ctx):
    translation = pick(list(product.translations.all()), ctx.chain, product.source_language)
    return translation


def fulfillment_flags(summary: str) -> dict:
    return {
        "fulfillment_type": summary,
        # Legacy two-value badge used by the existing UI: "stock" only when every sellable variant is Maral stock.
        "fulfillment": "stock" if summary == FulfillmentSummary.DIRECT else "order",
    }


# ----------------------------------------------------------------------------- card
def build_card(product: Product, ctx: CatalogContext) -> dict:
    proj = product.projection
    translation = _title(product, ctx)
    name = translation.name if translation else product.slug
    variants = product.active_variants
    images = product.published_images[:2]
    return {
        "id": str(product.public_id),
        "slug": product.slug,
        "name": name,
        "language": translation.language if translation else None,
        "is_machine_translated": bool(translation and translation.is_machine_translated),
        "brand": brand_ref(product.brand, ctx),
        "category": category_ref(product.category, ctx),
        "images": [image_ref(i, ctx, name) for i in images],
        "price": price_block(
            ctx,
            original_amount=proj.display_original_amount,
            original_currency=proj.display_original_currency,
            list_irr=proj.price_list_irr,
            final_irr=proj.price_final_irr,
            discount=proj.discount_percent,
            price_range=(proj.min_final_irr, proj.max_final_irr),
        ),
        "source": source_ref(proj.display_source),
        **fulfillment_flags(proj.fulfillment_summary),
        "available": proj.is_available,
        "availability_status": AvailabilityStatus.IN_STOCK.value
        if proj.is_available
        else AvailabilityStatus.OUT_OF_STOCK.value,
        "is_new": bool(proj.new_until and proj.new_until > _now()),
        "sizes": _axis(variants, ctx, "size"),
        "colors": _axis(variants, ctx, "color", with_swatch=True),
    }


def _now():
    from django.utils import timezone

    return timezone.now()


# ----------------------------------------------------------------------------- detail
def delivery_rules() -> dict:
    def build():
        rules = {}
        for r in DeliveryEstimateRule.objects.filter(is_active=True).select_related("source"):
            rules[(r.fulfillment_type, r.source.code if r.source else None)] = {
                "min_days": r.min_days,
                "max_days": r.max_days,
                "unit": r.unit,
            }
        return rules

    return cached("delivery_rules", {}, build)


def delivery_estimate(fulfillment_type: str, source_code: str | None):
    rules = delivery_rules()
    return rules.get((fulfillment_type, source_code)) or rules.get((fulfillment_type, None))


def _specifications(product: Product, ctx) -> list:
    rows = list(product.attribute_values.all())
    result, seen_text = [], {}
    for row in rows:
        names = list(row.attribute.translations.all())
        label_t = pick([t for t in names if t.language in ctx.chain] or names, ctx.chain)
        label = label_t.name if label_t else row.attribute.code
        if row.option_id:
            value = option_name(row.option, ctx)
        elif row.value_number is not None:
            value = plain_number(row.value_number)
        elif row.value_bool is not None:
            value = row.value_bool
        else:
            seen_text.setdefault(row.attribute_id, []).append((row, label))
            continue
        result.append({"code": row.attribute.code, "label": label, "value": value})
    for _attr, items in seen_text.items():
        by_lang = {r.language: (r, label) for r, label in items}
        for code in (*ctx.chain, product.source_language):
            if code in by_lang:
                row, label = by_lang[code]
                result.append({"code": row.attribute.code, "label": label, "value": row.value_text})
                break
    return result


def _breadcrumb(category: Category, ctx) -> list:
    ids = [int(x) for x in category.path.strip("/").split("/") if x]
    from django.db.models import Prefetch

    from apps.categories.models import CategoryTranslation

    nodes = Category.objects.filter(pk__in=ids).prefetch_related(
        Prefetch("translations", queryset=CategoryTranslation.objects.filter(language__in=list(ctx.chain)))
    )
    by_id = {n.pk: n for n in nodes}
    return [{"slug": by_id[i].slug, "name": category_name(by_id[i], ctx)} for i in ids if i in by_id]


def reviews_block(product: Product) -> dict:
    from django.db.models import Avg, Count

    from apps.reviews.models import Review

    qs = Review.objects.publishable().filter(product=product)
    agg = qs.aggregate(n=Count("pk"), avg=Avg("rating"))
    items = [
        {
            "id": str(r.public_id),
            "author_name": r.author_name,
            "rating": r.rating,
            "title": r.title,
            "body": r.body,
            "language": r.language,
            "verified_purchase": r.verified_purchase,
            "created_at": r.created_at,
        }
        for r in qs[:10]
    ]
    return {"count": agg["n"], "average": round(agg["avg"], 2) if agg["avg"] else None, "items": items}


def build_detail(product: Product, ctx: CatalogContext, related_cards: list) -> dict:
    card = build_card(product, ctx)
    proj = product.projection
    translation = _title(product, ctx)
    variants = []
    for v in product.active_variants:
        price = getattr(v, "price", None)
        if price is None:
            continue  # unpriced variants cannot be sold, so they are not offered
        options = {}
        for link in v.options.all():
            options[link.attribute.code] = {
                "code": link.option.code,
                "name": option_name(link.option, ctx),
                **({"value": link.option.swatch or None} if link.attribute.code == "color" else {}),
            }
        sellable = is_sellable(v)
        variants.append(
            {
                "id": str(v.public_id),
                "sku": v.sku,
                "size": options.get("size"),
                "color": options.get("color"),
                "options": options,
                "fulfillment_type": v.fulfillment_type,
                "source": source_ref(v.source),
                "available": sellable,
                "availability_status": (v.availability.status if getattr(v, "availability", None) else "unknown"),
                "price": {
                    **price_block(
                        ctx,
                        original_amount=price.original_amount,
                        original_currency=price.original_currency,
                        list_irr=price.list_irr,
                        final_irr=price.final_irr,
                        discount=price.discount_percent,
                    ),
                    "breakdown": price.breakdown,  # null until the pricing phase defines fees / FX
                },
                "delivery_estimate": delivery_estimate(v.fulfillment_type, v.source.code),
            }
        )
    display = (
        next((v for v in variants if v["id"] == str(proj.display_variant.public_id)), None)
        if proj.display_variant_id
        else None
    )
    category_nodes = _breadcrumb(product.category, ctx)
    all_images = [image_ref(i, ctx, card["name"]) for i in product.published_images]
    detail = {
        **card,
        "short_description": translation.short_description if translation else "",
        "description": translation.description if translation else "",
        "seo": {
            "title": (translation.seo_title if translation else "") or card["name"],
            "description": translation.seo_description if translation else "",
        },
        "images": all_images,
        "category": {**card["category"], "breadcrumb": category_nodes},
        "specifications": _specifications(product, ctx),
        "variants": variants,
        "price": {**card["price"], "breakdown": display["price"]["breakdown"] if display else None},
        "delivery_estimate": display["delivery_estimate"] if display else None,
        "reviews": reviews_block(product),
        "related": related_cards,
    }
    return detail
