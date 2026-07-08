"""Unit tests for the matching pipeline orchestration."""

from decimal import Decimal

from repricing_engine.matching.layers.ai_quality_gate import AiQualityGateLayer
from repricing_engine.matching.pipeline import MatchingPipeline
from repricing_engine.models.enums import Market, MatchMethod
from repricing_engine.models.product import CatalogProduct, CompetitorProduct
from tests.conftest import FakeGroqClient


def _catalog(**overrides) -> CatalogProduct:
    base = {
        "sku": "APL-IPH13",
        "ean": "4006381333931",
        "brand": "Apple",
        "title": "Apple iPhone 13 Blue",
        "category": "Phones",
        "market": Market.IT,
    }
    base.update(overrides)
    return CatalogProduct(**base)


def _competitor(**overrides) -> CompetitorProduct:
    base = {
        "source": "oxylabs",
        "source_id": "OXY-1",
        "title": "Apple iPhone 13 Blue",
        "price": Decimal("789.00"),
        "currency": "EUR",
        "url": "https://example.com",
        "market": Market.IT,
        "ean": "4006381333931",
        "brand": "Apple",
    }
    base.update(overrides)
    return CompetitorProduct(**base)


class TestMatchingPipeline:
    def test_exact_ean_match(self, settings, fake_encoder):
        pipeline = MatchingPipeline(settings, skip_ai=True, encoder=fake_encoder)
        result = pipeline.match_one(_catalog(), [_competitor()])

        assert result.is_matched
        assert result.best_match.match_method == MatchMethod.EXACT_EAN
        assert result.best_match.confidence == 0.95
        assert result.processing_time_ms >= 0.0

    def test_semantic_fallback_when_no_identifier(self, settings, fake_encoder):
        catalog = _catalog(ean=None, sku="ZZZ")
        competitor = _competitor(ean=None, sku=None, source_id="OXY-9")
        pipeline = MatchingPipeline(settings, skip_ai=True, encoder=fake_encoder)

        result = pipeline.match_one(catalog, [competitor])
        assert result.is_matched
        assert result.best_match.match_method == MatchMethod.SEMANTIC

    def test_min_confidence_filters_out_match(self, settings, fake_encoder):
        pipeline = MatchingPipeline(
            settings, skip_ai=True, min_confidence=0.99, encoder=fake_encoder
        )
        result = pipeline.match_one(_catalog(), [_competitor()])
        assert not result.is_matched
        assert len(result.rejected_candidates) == 1

    def test_ai_gate_can_reject(self, settings, fake_encoder):
        gate = AiQualityGateLayer(
            FakeGroqClient(payload={"is_match": False, "confidence_adjustment": -0.3})
        )
        pipeline = MatchingPipeline(settings, skip_ai=False, ai_gate=gate, encoder=fake_encoder)
        result = pipeline.match_one(_catalog(), [_competitor()])

        assert not result.is_matched
        assert result.rejected_candidates[0].match_details["ai_is_match"] is False

    def test_run_returns_one_result_per_product(self, settings, fake_encoder):
        pipeline = MatchingPipeline(settings, skip_ai=True, encoder=fake_encoder)
        results = pipeline.run([_catalog()], [_competitor()], show_progress=False)
        assert len(results) == 1

    def test_full_landscape_keeps_semantic_only_offers(self, settings, fake_encoder):
        """A strong match must not suppress other competitors' semantic scoring."""
        catalog = _catalog()
        strong = _competitor(source_id="A", ean="4006381333931")  # exact EAN -> 0.95
        semantic_only = _competitor(
            source_id="B",
            ean=None,
            sku=None,
            brand=None,
            title="Apple iPhone 13 Blue",  # same title -> high cosine
            url="https://other.it/p",
        )
        pipeline = MatchingPipeline(settings, skip_ai=True, encoder=fake_encoder)

        # Default (CSV-only) path: legacy global semantic-skip drops B.
        legacy = pipeline.match_one(catalog, [strong, semantic_only])
        assert {c.competitor_product.source_id for c in legacy.all_candidates} == {"A"}

        # Landscape path (--fetch): B still gets scored semantically and survives.
        landscape = pipeline.match_one(catalog, [strong, semantic_only], full_landscape=True)
        assert {c.competitor_product.source_id for c in landscape.all_candidates} == {"A", "B"}
