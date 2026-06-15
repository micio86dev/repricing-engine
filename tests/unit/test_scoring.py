"""Unit tests for confidence scoring helpers."""

from decimal import Decimal

import pytest

from repricing_engine.matching.scoring import (
    confidence_to_level,
    dedupe_candidates,
    select_best,
)
from repricing_engine.models.enums import ConfidenceLevel, Market, MatchMethod
from repricing_engine.models.product import CompetitorProduct, MatchCandidate


def _candidate(source_id: str, confidence: float) -> MatchCandidate:
    competitor = CompetitorProduct(
        source="oxylabs",
        source_id=source_id,
        title="Product",
        price=Decimal("10.00"),
        currency="EUR",
        url="https://example.com",
        market=Market.IT,
    )
    return MatchCandidate(
        competitor_product=competitor,
        confidence=confidence,
        match_method=MatchMethod.SEMANTIC,
        layer_source="test",
    )


class TestConfidenceToLevel:
    @pytest.mark.parametrize(
        ("confidence", "level"),
        [
            (0.95, ConfidenceLevel.HIGH),
            (0.901, ConfidenceLevel.HIGH),
            (0.90, ConfidenceLevel.MEDIUM),
            (0.80, ConfidenceLevel.MEDIUM),
            (0.70, ConfidenceLevel.MEDIUM),
            (0.699, ConfidenceLevel.LOW),
            (0.60, ConfidenceLevel.LOW),
        ],
    )
    def test_buckets(self, confidence, level):
        assert confidence_to_level(confidence) == level


class TestDedupeCandidates:
    def test_keeps_highest_per_competitor_sorted_desc(self):
        candidates = [
            _candidate("A", 0.60),
            _candidate("A", 0.90),  # higher dup for A
            _candidate("B", 0.75),
        ]
        deduped = dedupe_candidates(candidates)
        assert [c.competitor_product.source_id for c in deduped] == ["A", "B"]
        assert deduped[0].confidence == 0.90
        assert deduped[1].confidence == 0.75

    def test_empty(self):
        assert dedupe_candidates([]) == []


class TestSelectBest:
    def test_returns_max(self):
        best = select_best([_candidate("A", 0.6), _candidate("B", 0.8)])
        assert best.competitor_product.source_id == "B"

    def test_empty_returns_none(self):
        assert select_best([]) is None
