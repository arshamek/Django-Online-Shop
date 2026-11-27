"""
Estimated Maral price for an external (imported snapshot) product. Reuses the Phase 6 engine
(`pricing.services.engine.calculate`) and rule sets; there is NO second pricing engine here.

Three prices are never mixed up:
  original      the marketplace price in its own currency (a stored observation)
  estimated     what the Phase 6 engine says Maral would charge today - provisional unless every cost is known
  confirmed     only a checkout price quote creates one; this module never returns one (always null)

Missing shipping / customs are never treated as zero: when the active rule set has no applicable rule of that
kind the estimate is marked PROVISIONAL and lists them in `missing_costs`.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.catalog.context import money
from apps.pricing.services import rulesets
from apps.pricing.services.engine import PriceInput, calculate
from apps.pricing.services.inputs import effective_amounts

from ..constants import PriceStatus

REQUIRED_COST_KINDS = ("shipping", "customs")


def _empty(status: str, *, blockers=None, warnings=None, original=None) -> dict:
    return {
        "amount": None,
        "currency": "IRT",
        "status": status,
        "provisional": True,
        "missing_costs": [],
        "blockers": blockers or [],
        "warnings": warnings or [],
        "rule_version": None,
        "calculated_at": timezone.now().isoformat(),
        "original": original,
        "confirmed": None,
    }


def original_block(price) -> dict | None:
    if price is None:
        return None
    amount = (
        price.customer_amount
        if price.customer_amount is not None
        else (price.sale_amount if price.sale_amount is not None else price.list_amount)
    )
    return {
        "amount": format(amount.normalize(), "f"),
        "currency": price.currency_code,
        "list_amount": format(price.list_amount.normalize(), "f"),
        "observed_at": price.observed_at.isoformat(),
    }


def active_rule_set():
    return rulesets.current_ruleset_or_none()


def estimate(variant, product, *, rule_set, max_age_hours: int, now=None) -> dict:
    """`variant`: ExternalProductVariant; `product`: its ExternalProduct (select_related source/canonical_product)."""
    now = now or timezone.now()
    price = variant.current_price
    original = original_block(price)
    if price is None:
        return _empty(
            PriceStatus.UNAVAILABLE, blockers=[{"code": "no_source_price", "message": "No stored marketplace price."}]
        )
    if rule_set is None:
        return _empty(
            PriceStatus.INCOMPLETE,
            blockers=[{"code": "no_published_ruleset", "message": "No pricing rule set has been published."}],
            original=original,
        )
    effective, list_amount = effective_amounts(price, rule_set.source_price_basis)
    canonical = product.canonical_product
    canonical_variant = variant.canonical_variant
    inp = PriceInput(
        original_amount=effective,
        original_currency=price.currency_code,
        list_amount=list_amount,
        variant_id=canonical_variant.pk if canonical_variant else None,
        product_id=canonical.pk if canonical else None,
        brand_id=canonical.brand_id if canonical else None,
        category_id=canonical.category_id if canonical else None,
        category_path=canonical.category.path if canonical else "",
        source_id=product.source_id,
        source_country=product.source.country_code,
        fulfillment_type="PROXY_PURCHASE",
        weight_grams=canonical_variant.weight_grams if canonical_variant else None,
        destination_country=rule_set.default_destination_country,
        presentation_currency="IRT",
        at=now,
    )
    calc = calculate(inp, rule_set)
    out = _empty(PriceStatus.INCOMPLETE, original=original)
    out["rule_version"] = calc.rule_version
    out["warnings"] = list(calc.warnings)
    if calc.rate is not None:
        out["fx"] = calc.rate.as_dict()
    if calc.blockers or not calc.computed:
        out["blockers"] = [b.as_dict() for b in calc.blockers] or [
            {"code": "not_computed", "message": "The pricing engine could not compute a price.", "rule": ""}
        ]
        return out

    kinds = {c.kind for c in calc.components}
    missing = [k for k in REQUIRED_COST_KINDS if k not in kinds]
    unconfirmed = sorted({c.kind for c in calc.components if c.value_status and c.value_status != "confirmed"})
    stale = now - price.observed_at > timedelta(hours=max_age_hours)
    out["amount"] = money(calc.unit_price_irr, "IRT")["amount"]
    out["missing_costs"] = missing
    if calc.unit_separate_irr:
        out["separately_charged"] = money(calc.unit_separate_irr, "IRT")
    if unconfirmed:
        out["unconfirmed_costs"] = unconfirmed
    if stale:
        out["warnings"].append("stale_source_price")
    provisional = bool(missing or unconfirmed or calc.has_estimated or stale or calc.display_blockers)
    if calc.display_blockers:
        out["display_blockers"] = [b.as_dict() for b in calc.display_blockers]
    out["provisional"] = provisional
    out["status"] = PriceStatus.PROVISIONAL if provisional else PriceStatus.ESTIMATED
    out["price_irr"] = str(calc.unit_price_irr)  # internal use (budget filtering); stripped before output
    return out


def to_decimal_irr(block: dict) -> Decimal | None:
    value = block.get("price_irr")
    return Decimal(value) if value is not None else None
