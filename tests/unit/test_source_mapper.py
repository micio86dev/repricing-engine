"""Unit tests for mapping raw search results to competitor products."""

from decimal import Decimal

from repricing_engine.models.enums import Availability, Market
from repricing_engine.sources.mapper import to_competitor_product
from repricing_engine.sources.models import RawSearchResult


def _result(**overrides):
    base = {
        "source_provider": "searxng",
        "title": "Apple iPhone 13 128GB",
        "url": "https://www.shop.it/p/123",
    }
    base.update(overrides)
    return RawSearchResult(**base)


class TestMapper:
    def test_basic_mapping(self):
        result = _result(price=Decimal("789.00"), currency="eur", shipping_cost=Decimal("0"))
        competitor = to_competitor_product(result, Market.IT)
        assert competitor.source == "searxng"
        assert competitor.source_provider == "searxng"
        assert competitor.source_id.startswith("searxng-")
        assert competitor.price == Decimal("789.00")
        assert competitor.currency == "EUR"
        assert competitor.availability is Availability.UNKNOWN
        assert competitor.seller == "shop.it"

    def test_missing_price_allowed(self):
        competitor = to_competitor_product(_result(), Market.IT)
        assert competitor.price is None

    def test_currency_defaults_per_market(self):
        assert to_competitor_product(_result(), Market.UK).currency == "GBP"
        assert to_competitor_product(_result(), Market.US).currency == "USD"
        assert to_competitor_product(_result(), Market.DE).currency == "EUR"

    def test_invalid_ean_dropped(self):
        competitor = to_competitor_product(_result(ean="not-an-ean"), Market.IT)
        assert competitor.ean is None

    def test_valid_ean_kept(self):
        competitor = to_competitor_product(_result(ean="4006381333931"), Market.IT)
        assert competitor.ean == "4006381333931"

    def test_stable_source_id_per_url(self):
        first = to_competitor_product(_result(), Market.IT)
        second = to_competitor_product(_result(), Market.IT)
        assert first.source_id == second.source_id
