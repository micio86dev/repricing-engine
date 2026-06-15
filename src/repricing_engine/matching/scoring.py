"""Confidence scoring helpers: level bucketing, dedup and best-match selection."""

from repricing_engine.models.enums import ConfidenceLevel
from repricing_engine.models.product import MatchCandidate

_HIGH_THRESHOLD = 0.90
_MEDIUM_THRESHOLD = 0.70


def confidence_to_level(confidence: float) -> ConfidenceLevel:
    """Map a numeric confidence to a :class:`ConfidenceLevel` bucket.

    HIGH > 0.90, MEDIUM 0.70-0.90 (inclusive), LOW < 0.70.
    """
    if confidence > _HIGH_THRESHOLD:
        return ConfidenceLevel.HIGH
    if confidence >= _MEDIUM_THRESHOLD:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def dedupe_candidates(candidates: list[MatchCandidate]) -> list[MatchCandidate]:
    """Keep the single highest-confidence candidate per competitor product.

    Candidates from different layers may point at the same competitor; we keep
    the strongest one. The result is sorted by confidence, descending.

    Args:
        candidates: Raw candidates from all layers.

    Returns:
        Deduplicated candidates, highest confidence first.
    """
    best_by_competitor: dict[tuple[str, str], MatchCandidate] = {}
    for candidate in candidates:
        competitor = candidate.competitor_product
        key = (competitor.source, competitor.source_id)
        existing = best_by_competitor.get(key)
        if existing is None or candidate.confidence > existing.confidence:
            best_by_competitor[key] = candidate
    return sorted(best_by_competitor.values(), key=lambda c: c.confidence, reverse=True)


def select_best(candidates: list[MatchCandidate]) -> MatchCandidate | None:
    """Return the highest-confidence candidate, or ``None`` if empty."""
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.confidence)
