"""Step 5: deterministic clean-up of provider data. Never guesses a value; it only tidies what was given."""

import unicodedata
from dataclasses import replace

from apps.product_sources.providers.dto import ProviderImage, ProviderProduct, ProviderStock, ProviderVariant

_ALLOWED_STOCK = {"in_stock", "out_of_stock", "unknown"}


def _text(value: str, limit: int) -> str:
    cleaned = "".join(ch for ch in unicodedata.normalize("NFKC", value or "") if ch.isprintable() or ch in "\n\t ")
    return " ".join(cleaned.split())[:limit]


def _variant(v: ProviderVariant) -> ProviderVariant:
    price = v.price
    if price is not None:
        price = replace(price, currency=(price.currency or "").strip().upper())
    stock = v.stock
    if stock is not None:
        status = stock.status if stock.status in _ALLOWED_STOCK else "unknown"
        quantity = stock.quantity if stock.quantity is None or stock.quantity >= 0 else None
        stock = ProviderStock(status, quantity)
    return replace(
        v,
        external_variant_id=v.external_variant_id.strip(),
        sku=_text(v.sku, 120),
        barcode=_text(v.barcode, 120),
        size=_text(v.size, 80),
        color=_text(v.color, 80),
        price=price,
        stock=stock,
    )


def normalize_product(p: ProviderProduct) -> ProviderProduct:
    seen, images = set(), []
    for image in p.images:
        if image.url and image.url not in seen:
            seen.add(image.url)
            images.append(ProviderImage(image.url, len(images), image.width, image.height, _text(image.alt, 255)))
    return replace(
        p,
        external_id=p.external_id.strip(),
        title=_text(p.title, 500),
        brand=_text(p.brand, 200),
        category_path=_text(p.category_path, 500),
        description=_text(p.description, 20000),
        language=(p.language or "").strip().lower()[:5],
        manufacturer_code=_text(p.manufacturer_code, 80).upper(),
        variants=tuple(_variant(v) for v in p.variants),
        images=tuple(images),
    )
