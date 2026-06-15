"""Unit tests for the exact-identifier matching layer."""

from decimal import Decimal

from repricing_engine.matching.layers.exact_id import ExactIdLayer
from repricing_engine.models.enums import Market, MatchMethod
from repricing_engine.models.product import CatalogProduct, CompetitorProduct


def _competitor(**overrides) -> CompetitorProduct:
    base = {
        "source": "oxylabs",
        "source_id": "OXY-1",
        "title": "Some product",
        "price": Decimal("10.00"),
        "currency": "EUR",
        "url": "https://example.com",
        "market": Market.IT,
    }
    base.update(overrides)
    return CompetitorProduct(**base)


class TestExactIdLayer:
    def test_ean_match_same_brand(self, catalog_product):
        competitor = _competitor(ean="4006381333931", brand="Apple")
        candidates = ExactIdLayer().match(catalog_product, [competitor])

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.confidence == 0.95
        assert candidate.match_method == MatchMethod.EXACT_EAN
        assert candidate.match_details["brand_match"] is True

    def test_ean_match_brand_conflict_is_demoted(self, catalog_product):
        competitor = _competitor(ean="4006381333931", brand="Fakezon")
        candidates = ExactIdLayer().match(catalog_product, [competitor])

        assert candidates[0].confidence == 0.70
        assert candidates[0].match_details["brand_conflict"] is True

    def test_gtin_match_when_no_ean(self):
        catalog = CatalogProduct(
            sku="X1",
            ean=None,
            gtin="8000000000170",
            brand="Sony",
            title="Sony headphones",
            category="Audio",
            market=Market.IT,
        )
        competitor = _competitor(gtin="8000000000170", brand="Sony")
        candidates = ExactIdLayer().match(catalog, [competitor])

        assert candidates[0].match_method == MatchMethod.EXACT_GTIN
        assert candidates[0].confidence == 0.95

    def test_no_identifier_match(self, catalog_product):
        competitor = _competitor(ean="5901234123457", brand="Apple")
        assert ExactIdLayer().match(catalog_product, [competitor]) == []

    def test_custom_confidence_values(self, catalog_product):
        competitor = _competitor(ean="4006381333931", brand="Fakezon")
        layer = ExactIdLayer(base_confidence=0.99, brand_conflict_confidence=0.5)
        assert layer.match(catalog_product, [competitor])[0].confidence == 0.5
