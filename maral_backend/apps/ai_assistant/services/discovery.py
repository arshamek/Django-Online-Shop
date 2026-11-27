"""
Unified product discovery. One entry point, separate source adapters:

  maral              -> sources.internal   (Maral's own catalogue, live DB)
  trendyol / namshi  -> sources.snapshots  (stored imports only; live keyword search is disabled and reported)

A source failure never aborts the search: the other sources still answer and the failure is disclosed in
`provider_status`. A source is reported as searched only when a real search ran and succeeded.
"""

import logging
from dataclasses import dataclass, field

from ..constants import ALL_SOURCES, EXTERNAL_SOURCES, SOURCE_MARAL, ProviderStatus
from . import pricing_estimate
from .recommend import rank
from .sources import internal, snapshots
from .sources.base import Candidate, SearchSpec, SourceReport

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryResult:
    candidates: list = field(default_factory=list)
    reports: list = field(default_factory=list)
    relaxed: list = field(default_factory=list)
    spec: SearchSpec | None = None

    @property
    def products(self) -> list:
        return [c.data for c in self.candidates]


def _dedupe(internal_found: list, external_found: list) -> list:
    """Conservative: only a snapshot Phase 5 matched (AUTO/MANUAL) to a canonical product already in the results is
    folded into it. Similar names are never enough."""
    by_pk = {c.pk: c for c in internal_found}
    kept = []
    for cand in external_found:
        target = by_pk.get(cand.canonical_product_id) if cand.canonical_product_id else None
        if target is not None:
            target.data.setdefault("also_listed_at", []).append(
                {
                    "source": cand.source,
                    "ref": cand.data["ref"],
                    "kind": cand.data["kind"],
                    "estimated_maral_price": cand.data["prices"]["estimated_maral"],
                }
            )
            continue
        kept.append(cand)
    return kept


def discover(spec: SearchSpec, ctx, config) -> DiscoveryResult:
    result = DiscoveryResult(spec=spec)
    limit = config.max_results
    requested = [s for s in (spec.sources or ALL_SOURCES) if s in ALL_SOURCES]
    internal_found: list[Candidate] = []
    external_found: list[Candidate] = []
    rule_set = None
    if any(s in EXTERNAL_SOURCES for s in requested):
        try:
            rule_set = pricing_estimate.active_rule_set()
        except Exception:  # noqa: BLE001 - pricing problems must not break discovery; prices become "incomplete"
            logger.exception("ai: could not load the pricing rule set")

    for code in requested:
        if code not in config.enabled_sources:
            result.reports.append(
                SourceReport(
                    source=code, status=ProviderStatus.NOT_CONFIGURED, detail="This source is disabled by staff."
                )
            )
            continue
        if code == SOURCE_MARAL:
            try:
                found, relaxed = internal.search(spec, ctx, limit)
                internal_found = found
                report = SourceReport(
                    source=code,
                    status=ProviderStatus.AVAILABLE,
                    mode="internal_catalog",
                    searched=True,
                    result_count=len(found),
                    live_search="n/a",
                    relaxed=relaxed,
                )
                result.relaxed = sorted(set(result.relaxed) | set(relaxed))
            except Exception:  # noqa: BLE001
                logger.exception("ai: internal catalogue search failed")
                report = SourceReport(
                    source=code,
                    status=ProviderStatus.ERROR,
                    detail="The internal catalogue search failed.",
                    mode="internal_catalog",
                )
            result.reports.append(report)
        else:
            try:
                found, report = snapshots.search(spec, code=code, config=config, rule_set=rule_set, limit=limit)
                external_found.extend(found)
                result.relaxed = sorted(set(result.relaxed) | set(report.relaxed))
            except Exception:  # noqa: BLE001
                logger.exception("ai: snapshot search failed for %s", code)
                report = SourceReport(
                    source=code,
                    status=ProviderStatus.ERROR,
                    detail="Searching the stored snapshots failed.",
                    mode="imported_snapshot",
                )
            result.reports.append(report)

    kept_external = _dedupe(internal_found, external_found)
    ranked = rank(internal_found + kept_external, spec)[:limit]
    result.candidates = ranked
    counts: dict = {}
    for cand in ranked:
        counts[cand.source] = counts.get(cand.source, 0) + 1
    for report in result.reports:
        report.result_count = counts.get(report.source, 0) if report.searched else 0
    return result
