"""Classify a confirmed match into the client's match-tier vocabulary.

The pipeline overwrites ``match_method`` with ``AI_VERIFIED`` once the AI gate
confirms a candidate, so the *originating* layer is read from ``layer_source``
(which the gate preserves) to recover what was matched and how strongly.

Vocabulary (agreed with the client):
    confidence tier : PDP·GTIN | PDP·SKU | PDP·SKU·norm | snippet | title(oxy)
    match field     : gtin | sku | snippet
``PDP`` denotes an organically-scraped product page; ``(oxy)`` a record sourced
from OxyLabs.

When PDP verification has run, the *real* confirmation label is recorded on the
candidate's ``match_details`` (``confirmation_method``); :func:`confirmation_method_for`
prefers it and falls back to the :func:`confidence_tier_for` heuristic otherwise,
so the CSV-only path is unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repricing_engine.models.product import MatchCandidate

_EXACT_FUZZY_SCORE = 100.0
_OXYLABS_SOURCE = "oxylabs"


def match_field_for(candidate: MatchCandidate) -> str:
    """Return the identifier the match was established on: ``gtin``/``sku``/``snippet``."""
    layer = candidate.layer_source
    if layer == "exact_id":
        return "gtin"
    if layer == "sku_brand":
        return "sku"
    return "snippet"


def confidence_tier_for(candidate: MatchCandidate) -> str:
    """Return the match-strength/surface tag for a confirmed candidate."""
    layer = candidate.layer_source
    if layer == "exact_id":
        return "PDP·GTIN"
    if layer == "sku_brand":
        fuzzy = float(candidate.match_details.get("fuzzy_score", 0.0) or 0.0)
        return "PDP·SKU" if fuzzy >= _EXACT_FUZZY_SCORE else "PDP·SKU·norm"
    # semantic / title-based match
    if candidate.competitor_product.source == _OXYLABS_SOURCE:
        return "title(oxy)"
    return "snippet"


def confirmation_method_for(candidate: MatchCandidate) -> str:
    """Return the *real* PDP confirmation label, or the heuristic tier as fallback.

    When :class:`~repricing_engine.pdp.verifier.PdpVerifier` has confirmed the
    candidate against its product page, it records ``confirmation_method`` in
    ``match_details`` (e.g. ``PDP·GTIN``, ``json_ld``). With no PDP run, this
    falls back to :func:`confidence_tier_for`, leaving CSV-only output unchanged.
    """
    method = candidate.match_details.get("confirmation_method")
    return str(method) if method else confidence_tier_for(candidate)
