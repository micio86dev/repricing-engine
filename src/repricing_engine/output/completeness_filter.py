"""Filter match results down to offers with a fully known landed cost.

A "complete" offer is one whose competitor product carries both a price and a
shipping cost (``0``/free counts as known; ``None`` does not). Requesting this
filter drops every price-less or shipping-less offer, so the output holds only
offers whose landed price is certain. A product left with no complete offer
becomes unmatched (``best_match=None``), which the writer renders as a
placeholder row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repricing_engine.models.match_result import MatchResult
    from repricing_engine.models.product import MatchCandidate


def _has_price_and_shipping(candidate: MatchCandidate) -> bool:
    """True when the candidate's offer has both a price and a shipping cost."""
    competitor = candidate.competitor_product
    return competitor.price is not None and competitor.shipping_cost is not None


def keep_complete_offers(results: list[MatchResult]) -> list[MatchResult]:
    """Return copies of ``results`` keeping only price+shipping-complete offers.

    Each result's ``all_candidates`` is filtered to complete offers. The
    ``best_match`` is preserved when it is still complete, otherwise it is
    recomputed as the highest-confidence surviving offer, or ``None`` when the
    product has no complete offer left. Inputs are never mutated.
    """
    filtered: list[MatchResult] = []
    for result in results:
        complete = [c for c in result.all_candidates if _has_price_and_shipping(c)]
        if result.best_match is not None and _has_price_and_shipping(result.best_match):
            best = result.best_match
        else:
            best = max(complete, key=lambda c: c.confidence, default=None)
        filtered.append(result.model_copy(update={"all_candidates": complete, "best_match": best}))
    return filtered
