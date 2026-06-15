"""Unit tests for the semantic matching layer (with a stubbed encoder)."""

from decimal import Decimal

from repricing_engine.matching.layers.semantic import SemanticLayer
from repricing_engine.models.enums import Market, MatchMethod
from repricing_engine.models.product import CatalogProduct, CompetitorProduct


def _catalog() -> CatalogProduct:
    return CatalogProduct(
        sku="APL-IPH13",
        brand="Apple",
        title="Apple iPhone 13 128GB Blue",
        category="Smartphones",
        market=Market.IT,
    )


def _competitor(source_id: str, title: str, brand: str | None) -> CompetitorProduct:
    return CompetitorProduct(
        source="oxylabs",
        source_id=source_id,
        title=title,
        price=Decimal("10.00"),
        currency="EUR",
        url="https://example.com",
        market=Market.IT,
        brand=brand,
    )


class TestSemanticLayer:
    def test_similar_product_scores_higher_than_unrelated(self, fake_encoder):
        layer = SemanticLayer(encoder=fake_encoder)
        similar = _competitor("OXY-1", "Apple iPhone 13 128GB Blue", "Apple")
        unrelated = _competitor("OXY-2", "Bosch Washing Machine WAU28", "Bosch")

        candidates = {
            c.competitor_product.source_id: c for c in layer.match(_catalog(), [similar, unrelated])
        }

        assert candidates["OXY-1"].confidence > candidates["OXY-2"].confidence
        assert candidates["OXY-1"].match_method == MatchMethod.SEMANTIC

    def test_empty_competitors(self, fake_encoder):
        assert SemanticLayer(encoder=fake_encoder).match(_catalog(), []) == []

    def test_embeddings_are_cached(self, fake_encoder):
        layer = SemanticLayer(encoder=fake_encoder)
        competitor = _competitor("OXY-1", "Apple iPhone 13", "Apple")
        layer.match(_catalog(), [competitor])
        # catalog text + competitor text cached (2 unique texts).
        assert len(layer._cache) == 2

    def test_disabled_when_encoder_cannot_load(self):
        layer = SemanticLayer(model_name="definitely/not-a-real-model", encoder=None)
        layer._encoder_failed = True  # simulate a failed lazy load
        assert layer.match(_catalog(), [_competitor("OXY-1", "x", "y")]) == []
