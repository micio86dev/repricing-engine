"""Unit tests for the SKU + brand fuzzy matching layer."""

from decimal import Decimal

from repricing_engine.matching.layers.sku_brand import SkuBrandLayer
from repricing_engine.models.enums import Market, MatchMethod
from repricing_engine.models.product import CatalogProduct, CompetitorProduct


def _catalog(**overrides) -> CatalogProduct:
    base = {
        "sku": "ABC-123",
        "brand": "Logitech",
        "title": "Mouse",
        "category": "Accessories",
        "market": Market.IT,
    }
    base.update(overrides)
    return CatalogProduct(**base)


def _competitor(**overrides) -> CompetitorProduct:
    base = {
        "source": "oxylabs",
        "source_id": "OXY-1",
        "title": "Mouse",
        "price": Decimal("10.00"),
        "currency": "EUR",
        "url": "https://example.com",
        "market": Market.IT,
    }
    base.update(overrides)
    return CompetitorProduct(**base)


class TestSkuBrandLayer:
    def test_exact_sku_same_brand_is_top_confidence(self):
        candidate = SkuBrandLayer().match(
            _catalog(), [_competitor(sku="ABC-123", brand="Logitech")]
        )[0]
        assert candidate.confidence == 1.0
        assert candidate.match_method == MatchMethod.SKU_BRAND
        assert candidate.match_details["brand_state"] == "match"

    def test_fuzzy_sku_same_brand(self):
        candidate = SkuBrandLayer().match(
            _catalog(), [_competitor(sku="ABC123", brand="Logitech")]
        )[0]
        assert 0.6 < candidate.confidence < 1.0

    def test_brand_conflict_applies_penalty(self):
        candidate = SkuBrandLayer().match(_catalog(), [_competitor(sku="ABC-123", brand="Razer")])[
            0
        ]
        # Exact SKU (1.0) * conflict boost (0.3)
        assert candidate.confidence == 0.3
        assert candidate.match_details["brand_state"] == "conflict"

    def test_missing_competitor_brand_uses_partial_boost(self):
        candidate = SkuBrandLayer().match(_catalog(), [_competitor(sku="ABC-123", brand=None)])[0]
        assert candidate.confidence == 0.7
        assert candidate.match_details["brand_state"] == "missing"

    def test_competitor_without_sku_is_skipped(self):
        assert SkuBrandLayer().match(_catalog(), [_competitor(sku=None)]) == []

    def test_catalog_without_sku_returns_empty(self):
        assert SkuBrandLayer().match(_catalog(sku=""), [_competitor(sku="ABC-123")]) == []
