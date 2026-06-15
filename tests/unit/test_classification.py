"""Unit tests for match-tier / match-field classification."""

from decimal import Decimal

from repricing_engine.matching.classification import confidence_tier_for, match_field_for
from repricing_engine.models.enums import Market, MatchMethod
from repricing_engine.models.product import CompetitorProduct, MatchCandidate


def _candidate(layer_source: str, *, fuzzy: float | None = None, source: str = "organica"):
    competitor = CompetitorProduct(
        source=source,
        source_id="X",
        title="Comp",
        price=Decimal("10"),
        currency="EUR",
        url="https://e.com/x",
        market=Market.IT,
    )
    details = {} if fuzzy is None else {"fuzzy_score": fuzzy}
    return MatchCandidate(
        competitor_product=competitor,
        confidence=0.95,
        match_method=MatchMethod.AI_VERIFIED,  # gate masks the original method
        layer_source=layer_source,
        match_details=details,
    )


class TestClassification:
    def test_gtin_from_exact_id(self):
        candidate = _candidate("exact_id")
        assert match_field_for(candidate) == "gtin"
        assert confidence_tier_for(candidate) == "PDP·GTIN"

    def test_exact_sku(self):
        candidate = _candidate("sku_brand", fuzzy=100)
        assert match_field_for(candidate) == "sku"
        assert confidence_tier_for(candidate) == "PDP·SKU"

    def test_normalized_sku(self):
        candidate = _candidate("sku_brand", fuzzy=88)
        assert confidence_tier_for(candidate) == "PDP·SKU·norm"

    def test_semantic_snippet_organic(self):
        candidate = _candidate("semantic")
        assert match_field_for(candidate) == "snippet"
        assert confidence_tier_for(candidate) == "snippet"

    def test_semantic_title_from_oxylabs(self):
        candidate = _candidate("semantic", source="oxylabs")
        assert confidence_tier_for(candidate) == "title(oxy)"
