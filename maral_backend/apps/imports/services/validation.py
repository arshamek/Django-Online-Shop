"""
Step 4 (business half): is this provider product usable? Structural validation of the *response* lives in
the providers (InvalidProviderResponse / RejectedItem); this checks the content of a well-formed product.
Variant-level problems exclude that variant; product-level errors fail the item.
"""

from dataclasses import dataclass, field

from django.conf import settings

from apps.product_sources.providers.dto import ProviderProduct, ProviderVariant


@dataclass
class Issue:
    code: str
    message: str


@dataclass
class ValidationReport:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    usable_variants: list[ProviderVariant] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_product(p: ProviderProduct) -> ValidationReport:
    report = ValidationReport()
    if not p.external_id:
        report.errors.append(Issue("missing_external_id", "The product has no source identifier."))
    if not p.title:
        report.errors.append(Issue("missing_title", "The product has no title."))
    if not p.variants:
        report.errors.append(Issue("no_variants", "The product has no variants."))
    ids = [v.external_variant_id for v in p.variants]
    if len(ids) != len(set(ids)):
        report.errors.append(Issue("duplicate_variant_ids", "Two variants share a source identifier."))

    currencies = {c.upper() for c in settings.IMPORTS_SUPPORTED_CURRENCIES}
    for v in p.variants:
        price = v.price
        if price is None:
            report.warnings.append(
                Issue("variant_missing_price", f"Variant {v.external_variant_id} has no valid price.")
            )
        elif price.currency not in currencies:
            report.warnings.append(
                Issue(
                    "variant_unsupported_currency",
                    f"Variant {v.external_variant_id}: currency '{price.currency}' is not supported.",
                )
            )
        elif price.list_amount <= 0:
            report.warnings.append(
                Issue("variant_invalid_price", f"Variant {v.external_variant_id}: price must be above zero.")
            )
        elif price.sale_amount is not None and price.sale_amount > price.list_amount:
            report.warnings.append(
                Issue("variant_invalid_price", f"Variant {v.external_variant_id}: sale price exceeds list price.")
            )
        else:
            report.usable_variants.append(v)
    if p.variants and not report.usable_variants and not report.errors:
        report.errors.append(Issue("no_importable_variants", "No variant has a usable price."))

    if not p.brand:
        report.warnings.append(Issue("missing_brand", "No brand supplied; staff must resolve it."))
    if not p.images:
        report.warnings.append(Issue("missing_images", "No image metadata supplied."))
    elif any(not i.url.startswith("https://") for i in p.images):
        report.warnings.append(Issue("image_url_not_absolute", "Some image URLs are not absolute https URLs."))
    if not p.description:
        report.warnings.append(Issue("missing_description", "No description supplied."))
    return report
