import hashlib
import json

from apps.product_sources.providers.dto import ProviderProduct


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def payload_hash(product: ProviderProduct) -> str:
    """Change detection over everything we received (prices and stock included)."""
    return _digest(product.raw)


def content_hash(product: ProviderProduct) -> str:
    """Text, attributes and images only: price or stock moves must not trigger re-translation."""
    return _digest(
        [
            product.title,
            product.description,
            product.brand,
            product.category_path,
            product.language,
            product.attributes,
            [i.url for i in product.images],
        ]
    )


def value_hash(*parts) -> str:
    return _digest([str(p) for p in parts])
