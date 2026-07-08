"""Unit tests for market auto-detection."""

from repricing_engine.models.enums import Market
from repricing_engine.normalization.market import DEFAULT_MARKET, detect_market


class TestDetectMarket:
    def test_explicit_catalog_market_column_wins(self):
        catalog = [{"sku": "A", "market": "DE"}, {"sku": "B", "market": "DE"}]
        # Competitor TLDs disagree, but the explicit catalog column takes priority.
        competitors = [{"url": "https://shop.it/p"}]
        assert detect_market(catalog, competitors) == Market.DE

    def test_localized_country_header(self):
        catalog = [{"sku": "A", "mercato": "FR"}]
        assert detect_market(catalog) == Market.FR

    def test_falls_back_to_competitor_tld(self):
        catalog = [{"sku": "A", "title": "Widget"}]  # no market column
        competitors = [
            {"url": "https://www.shop.de/p1"},
            {"url": "https://www.shop.de/p2"},
            {"url": "https://www.other.it/p3"},
        ]
        assert detect_market(catalog, competitors) == Market.DE

    def test_co_uk_tld(self):
        catalog = [{"sku": "A"}]
        competitors = [{"url": "https://www.shop.co.uk/p"}]
        assert detect_market(catalog, competitors) == Market.UK

    def test_currency_fallback(self):
        catalog = [{"sku": "A"}]
        competitors = [{"price": "10.00", "currency": "GBP"}]  # .com TLD-less / ambiguous
        assert detect_market(catalog, competitors) == Market.UK

    def test_default_when_inconclusive(self):
        assert (
            detect_market([{"sku": "A"}], [{"price": "10.00", "currency": "EUR"}]) == DEFAULT_MARKET
        )

    def test_default_with_no_competitors(self):
        assert detect_market([{"sku": "A", "title": "X"}]) == DEFAULT_MARKET
