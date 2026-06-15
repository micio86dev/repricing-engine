"""Unit tests for the AI quality gate (with a stubbed Groq client)."""

from decimal import Decimal

from repricing_engine.matching.layers.ai_quality_gate import AiQualityGateLayer
from repricing_engine.models.enums import Market, MatchMethod
from repricing_engine.models.product import CompetitorProduct, MatchCandidate
from tests.conftest import FakeGroqClient


def _candidate(confidence: float = 0.80) -> MatchCandidate:
    competitor = CompetitorProduct(
        source="oxylabs",
        source_id="OXY-1",
        title="Apple iPhone 13",
        price=Decimal("789.00"),
        currency="EUR",
        url="https://example.com",
        market=Market.IT,
        ean="4006381333931",
        brand="Apple",
    )
    return MatchCandidate(
        competitor_product=competitor,
        confidence=confidence,
        match_method=MatchMethod.SKU_BRAND,
        layer_source="sku_brand",
    )


class TestAiQualityGate:
    def test_confirms_and_adjusts_confidence(self, catalog_product):
        client = FakeGroqClient(
            payload={"is_match": True, "confidence_adjustment": 0.1, "reason": "same item"}
        )
        gate = AiQualityGateLayer(client)
        reviewed = gate.review(catalog_product, [_candidate(0.80)])[0]

        assert reviewed.match_details["ai_is_match"] is True
        assert reviewed.confidence == 0.90
        assert reviewed.match_method == MatchMethod.AI_VERIFIED

    def test_rejection_keeps_original_method(self, catalog_product):
        client = FakeGroqClient(
            payload={"is_match": False, "confidence_adjustment": -0.3, "reason": "different"}
        )
        reviewed = AiQualityGateLayer(client).review(catalog_product, [_candidate(0.80)])[0]
        assert reviewed.match_details["ai_is_match"] is False
        assert reviewed.match_method == MatchMethod.SKU_BRAND
        assert reviewed.confidence == 0.50

    def test_adjustment_is_clamped(self, catalog_product):
        client = FakeGroqClient(
            payload={"is_match": True, "confidence_adjustment": 5.0, "reason": "x"}
        )
        reviewed = AiQualityGateLayer(client).review(catalog_product, [_candidate(0.80)])[0]
        assert reviewed.confidence == 0.90  # +0.1 max adjustment

    def test_groq_failure_is_skipped_and_retried(self, catalog_product):
        client = FakeGroqClient(error=RuntimeError("groq down"))
        gate = AiQualityGateLayer(client)
        reviewed = gate.review(catalog_product, [_candidate(0.80)])[0]

        assert reviewed.confidence == 0.80  # unchanged
        assert reviewed.match_details["ai_gate"] == "skipped"
        assert client.completions.call_count == 3  # retried

    def test_malformed_payload_is_skipped(self, catalog_product):
        client = FakeGroqClient(payload={"reason": "missing keys"})
        reviewed = AiQualityGateLayer(client).review(catalog_product, [_candidate(0.80)])[0]
        assert reviewed.match_details["ai_gate"] == "skipped"
