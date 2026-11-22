"""Step 7: save the source listing (raw data, variants, append-only price/availability history)."""

from dataclasses import dataclass

from django.db import IntegrityError, transaction

from apps.common.text import normalize_search_text
from apps.product_sources.models import (
    DataOrigin,
    ExternalProduct,
    ExternalProductImage,
    ExternalProductVariant,
    ListingStatus,
    SourceAvailability,
    SourcePrice,
)
from apps.product_sources.providers.dto import ProviderProduct

from .hashing import content_hash, payload_hash, value_hash
from .mapping import brand_key


@dataclass
class ListingChange:
    ext: ExternalProduct
    created: bool
    payload_changed: bool
    content_changed: bool


def _fields(p: ProviderProduct, usable, data_origin: str, now) -> dict:
    return {
        "external_url": p.url[:500],
        "language": p.language,
        "raw_title": p.title,
        "raw_brand": p.brand,
        "raw_category_id": p.category_id,
        "raw_category_path": p.category_path,
        "raw_description": p.description,
        "raw_attributes": p.attributes,
        "raw_payload": p.raw,
        "payload_hash": payload_hash(p),
        "content_hash": content_hash(p),
        "brand_key": brand_key(p.brand),
        "manufacturer_code": p.manufacturer_code,
        "gender": p.gender,
        "data_origin": data_origin,
        "last_imported_at": now,
    }


def upsert_listing(source, p: ProviderProduct, usable, *, data_origin: str, gtin_trusted: bool, now) -> ListingChange:
    ext = ExternalProduct.objects.select_for_update().filter(source=source, external_id=p.external_id).first()
    created = ext is None
    new_status = ListingStatus.ACTIVE if p.is_listed else ListingStatus.DELISTED
    if created:
        try:
            with transaction.atomic():
                ext = ExternalProduct.objects.create(
                    source=source,
                    external_id=p.external_id,
                    status=new_status,
                    first_seen_at=now,
                    last_seen_at=now,
                    **_fields(p, usable, data_origin, now),
                )
        except IntegrityError:  # another worker created it first
            ext = ExternalProduct.objects.select_for_update().get(source=source, external_id=p.external_id)
            created = False
    fields = _fields(p, usable, data_origin, now)
    payload_changed = created or ext.payload_hash != fields["payload_hash"] or ext.status != new_status
    content_changed = created or ext.content_hash != fields["content_hash"]
    if not created:
        ext.last_seen_at = now
        ext.status = new_status
        if payload_changed:
            for name, value in fields.items():
                setattr(ext, name, value)
        ext.save()
    if payload_changed:
        if content_changed:
            ext.images.all().delete()
            ExternalProductImage.objects.bulk_create(
                ExternalProductImage(
                    external_product=ext,
                    position=i.position,
                    url=i.url[:500],
                    width=i.width,
                    height=i.height,
                    alt=i.alt,
                )
                for i in p.images
            )
        _upsert_variants(ext, p, usable, gtin_trusted, now)
    return ListingChange(ext, created, payload_changed, content_changed)


def _upsert_variants(ext: ExternalProduct, p: ProviderProduct, usable, gtin_trusted: bool, now) -> None:
    usable_ids = {v.external_variant_id for v in usable}
    seen = set()
    for v in p.variants:
        seen.add(v.external_variant_id)
        ev, _ = ExternalProductVariant.objects.get_or_create(
            external_product=ext, external_variant_id=v.external_variant_id
        )
        ev.sku, ev.barcode, ev.gtin = v.sku, v.barcode, v.gtin
        ev.gtin_trusted = bool(v.gtin) and gtin_trusted
        ev.size_raw, ev.color_raw = v.size, v.color
        ev.size_key, ev.color_key = normalize_search_text(v.size)[:80], normalize_search_text(v.color)[:80]
        ev.attributes, ev.source_url, ev.is_active = v.attributes, v.url[:500], v.is_active
        if v.external_variant_id in usable_ids and v.price is not None:
            digest = value_hash(v.price.list_amount, v.price.sale_amount, v.price.customer_amount, v.price.currency)
            if ev.current_price is None or ev.current_price.value_hash != digest:
                ev.current_price = SourcePrice.objects.create(
                    variant=ev,
                    list_amount=v.price.list_amount,
                    sale_amount=v.price.sale_amount,
                    customer_amount=v.price.customer_amount,
                    currency_code=v.price.currency,
                    value_hash=digest,
                    observed_at=now,
                )
        if v.stock is not None:
            current = ev.current_availability
            if current is None or (current.status, current.quantity_hint) != (v.stock.status, v.stock.quantity):
                ev.current_availability = SourceAvailability.objects.create(
                    variant=ev, status=v.stock.status, quantity_hint=v.stock.quantity, observed_at=now
                )
        ev.save()
    ExternalProductVariant.objects.filter(external_product=ext).exclude(external_variant_id__in=seen).update(
        is_active=False
    )


def mark_delisted(ext: ExternalProduct, now) -> None:
    ext.status = ListingStatus.DELISTED
    ext.last_seen_at = now
    ext.save(update_fields=["status", "last_seen_at", "updated_at"])
    for ev in ext.variants.filter(is_active=True):
        current = ev.current_availability
        if current is None or current.status != "out_of_stock":
            ev.current_availability = SourceAvailability.objects.create(
                variant=ev, status="out_of_stock", quantity_hint=0, observed_at=now
            )
            ev.save(update_fields=["current_availability"])


__all__ = ["ListingChange", "upsert_listing", "mark_delisted", "DataOrigin"]
