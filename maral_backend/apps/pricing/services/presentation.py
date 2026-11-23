"""Customer-safe views of a calculation. Profit, margin and internal cost are never included."""

from ..models import BreakdownVisibility, ChargeMode

_HIDDEN_KINDS = {"profit", "rounding"}


def customer_breakdown(calc, visibility: str):
    """Stored in VariantPrice.breakdown (served by the catalogue API). Amounts are IRR strings."""
    if visibility == BreakdownVisibility.NONE or not calc.computed:
        return None
    visible = [c for c in calc.components if c.kind not in _HIDDEN_KINDS and c.kind != "item"]
    base = {
        "unit": "IRR",
        "original": {"amount": str(calc.original_amount), "currency": calc.original_currency},
        "rate": {
            "value": str(calc.rate.rate),
            "as_of": calc.rate.rate_at.isoformat() if calc.rate.rate_at else None,
            "pair": f"{calc.rate.base}/{calc.rate.quote}",
        },
        "item_irr": str(calc.item_irr),
        "has_estimated_costs": calc.has_estimated,
    }
    if visibility == BreakdownVisibility.SUMMARY:
        base["costs_irr"] = str(
            sum((c.amount_irr for c in visible if c.charge_mode != ChargeMode.ABSORBED), start=calc.item_irr * 0)
        )
        return base
    base["lines"] = [
        {
            "kind": c.kind,
            "name": c.name,
            "amount_irr": str(c.amount_irr),
            "estimated": c.value_status == "estimated",
            "billed_separately": c.charge_mode == ChargeMode.SEPARATE,
        }
        for c in visible
        if c.charge_mode != ChargeMode.ABSORBED
    ]
    return base
