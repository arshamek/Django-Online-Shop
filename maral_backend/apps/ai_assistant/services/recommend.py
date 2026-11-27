"""Ranking, "why this matches" reasons and comparison. Uses only values present in the retrieved product data."""

from decimal import Decimal

from ..constants import Availability, PriceStatus
from .sources.base import Candidate, SearchSpec
from .textnorm import fold

INF = Decimal("1e30")


def _has(text: str, needle: str) -> bool:
    return bool(needle) and fold(needle) in text


def score(cand: Candidate, spec: SearchSpec) -> Candidate:
    d = cand.data
    title = fold(d.get("title") or "")
    brand = fold(d.get("brand") or "")
    blob = " ".join([title, fold(d.get("description") or ""), fold(d.get("category") or "")])
    s, matched, unmet = 0.0, [], []

    if spec.brands or spec.brand_ids:
        if cand.kind == "internal" and spec.brand_ids or any(_has(brand + " " + title, b) for b in spec.brands):
            matched.append("brand")
            s += 3
        else:
            unmet.append("brand")
    if spec.concept:
        matched.append("category")
        s += 2
    if spec.colors:
        codes = {(v.get("color_code") or "").lower() for v in d["variants"]} | {
            fold(v.get("color") or "") for v in d["variants"]
        }
        if codes & set(spec.colors):
            matched.append("color")
            s += 1
        else:
            unmet.append("color")
    if spec.sizes:
        usable = [
            v
            for v in d["variants"]
            if ((v.get("size_code") or "").lower() in spec.sizes or fold(v.get("size") or "") in spec.sizes)
            and v["availability"] != Availability.OUT_OF_STOCK
        ]
        if usable:
            matched.append("size")
            s += 1
        else:
            unmet.append("size")
    for use in spec.uses:
        from . import vocab

        if any(t in blob for t in vocab.USES[use]):
            matched.append(f"use:{use}")
            s += 1
    if (spec.min_irr is not None or spec.max_irr is not None) and cand.price_irr is not None:
        matched.append("budget")
        s += 1

    availability = d["availability"]["status"]
    s += {Availability.IN_STOCK: 2, Availability.OUT_OF_STOCK: -3}.get(availability, 0)
    status = d["prices"]["estimated_maral"]["status"]
    if status == PriceStatus.PROVISIONAL:
        s -= 0.5
    elif status in (PriceStatus.INCOMPLETE, PriceStatus.UNAVAILABLE):
        s -= 1.5
    if (d.get("snapshot") or {}).get("stale"):
        s -= 1
    if "fixture_data_not_real" in d.get("warnings", []):
        s -= 1
    if cand.kind == "internal":
        s += 1  # source reliability: Maral's own, continuously maintained catalogue
    cand.score, cand.matched, cand.unmet = s, matched, unmet
    d["match"] = {"matched": matched, "unmet": unmet, "score": round(s, 2)}
    return cand


def rank(cands: list, spec: SearchSpec) -> list:
    scored = [score(c, spec) for c in cands]
    # an out-of-stock result is only shown when nothing else is
    in_play = [c for c in scored if c.data["availability"]["status"] != Availability.OUT_OF_STOCK] or scored
    if spec.sort == "price_asc":
        return sorted(in_play, key=lambda c: (c.price_irr is None, c.price_irr or INF, -c.score))
    return sorted(in_play, key=lambda c: (-c.score, c.price_irr if c.price_irr is not None else INF))


# ------------------------------------------------------------------------------------------------ comparison
ROWS = (
    "source",
    "brand",
    "category",
    "estimated_price",
    "price_status",
    "original_price",
    "availability",
    "sizes",
    "colors",
    "snapshot_age",
)


def _cell(d: dict, row: str):
    prices = d["prices"]
    est = prices["estimated_maral"]
    if row == "source":
        return d["source"]["code"]
    if row in ("brand", "category"):
        return d.get(row)
    if row == "estimated_price":
        return {"amount": est["amount"], "currency": "IRT"} if est.get("amount") is not None else None
    if row == "price_status":
        return est.get("status")
    if row == "original_price":
        o = prices.get("original")
        return {"amount": o["amount"], "currency": o["currency"]} if o and o.get("amount") else None
    if row == "availability":
        a = d["availability"]
        return {"status": a["status"], "last_known": a.get("last_known"), "confidence": a.get("confidence")}
    if row == "sizes":
        sizes = d.get("sizes") or []
        return [s["name"] if isinstance(s, dict) else s for s in sizes] or None
    if row == "colors":
        colors = d.get("colors") or []
        return [c["name"] if isinstance(c, dict) else c for c in colors] or None
    if row == "snapshot_age":
        snap = d.get("snapshot")
        return snap.get("price_age_hours") if snap else None
    return None


def compare(cands: list) -> dict:
    """Only attributes present in the data; a missing value is null (shown as 'unavailable'), never guessed."""
    rows = [{"attribute": row, "values": [_cell(c.data, row) for c in cands]} for row in ROWS]
    priced = [
        (c.price_irr, c.data["ref"])
        for c in cands
        if c.price_irr is not None
        and c.data["prices"]["estimated_maral"]["status"]
        in (PriceStatus.LISTED, PriceStatus.ESTIMATED, PriceStatus.PROVISIONAL)
    ]
    cheapest = min(priced, key=lambda p: p[0])[1] if len(priced) >= 2 else None
    return {
        "products": [
            {"ref": c.data["ref"], "title": c.data["title"], "source": c.data["source"]["code"]} for c in cands
        ],
        "rows": rows,
        "cheapest_estimated": cheapest,
        "null_means": "not provided by the source; it is not guessed",
    }
