"""Unit tests for the TrovaPrezzi source provider (no network — MockTransport)."""

from decimal import Decimal

import httpx

from repricing_engine.models.enums import Market
from repricing_engine.models.product import CatalogProduct
from repricing_engine.pdp.models import PdpExtractionResult
from repricing_engine.sources.providers.trovaprezzi import TrovaPrezziProvider


def _catalog(market: Market = Market.IT) -> CatalogProduct:
    return CatalogProduct(
        sku="APL-IPH13-128",
        brand="Apple",
        title="Apple iPhone 13 128GB Blue",
        category="Smartphones",
        market=market,
    )


class _FakeExtractor:
    """Stand-in AI extractor returning a fixed offer."""

    def __init__(self, result: PdpExtractionResult | None) -> None:
        self._result = result
        self.calls = 0

    async def extract(
        self, html: str, product: CatalogProduct, url: str
    ) -> PdpExtractionResult | None:
        self.calls += 1
        return self._result


class TestTrovaPrezzi:
    async def test_non_it_market_returns_empty(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, text="<html></html>"))
        async with client:
            provider = TrovaPrezziProvider(client)
            assert await provider.search(_catalog(Market.DE), Market.DE) == []

    async def test_parses_offer_rows(self, mock_async_client, fixture_text):
        html = fixture_text("trovaprezzi_product_page.html")
        client = mock_async_client(lambda request: httpx.Response(200, text=html))
        async with client:
            provider = TrovaPrezziProvider(client)
            results = await provider.search(_catalog(), Market.IT)

        assert len(results) == 3
        by_price = sorted(r.price for r in results)
        assert by_price == [Decimal("779.90"), Decimal("789.00"), Decimal("799.00")]
        # "Spedizione gratis"/"gratuita" -> 0, "5,90 €" -> 5.90.
        shipping = sorted(r.shipping_cost for r in results)
        assert shipping == [Decimal("0"), Decimal("0"), Decimal("5.90")]
        assert all(r.source_provider == "trovaprezzi" for r in results)

    async def test_ai_fallback_when_no_offers(self, mock_async_client):
        client = mock_async_client(
            lambda request: httpx.Response(200, text="<html><body>no offers here</body></html>")
        )
        extractor = _FakeExtractor(PdpExtractionResult(price=Decimal("100.00"), currency="EUR"))
        async with client:
            provider = TrovaPrezziProvider(client, ai_extractor=extractor)
            results = await provider.search(_catalog(), Market.IT)

        assert extractor.calls == 1
        assert len(results) == 1
        assert results[0].price == Decimal("100.00")

    async def test_no_offers_and_no_extractor_returns_empty(self, mock_async_client):
        client = mock_async_client(
            lambda request: httpx.Response(200, text="<html><body>nothing</body></html>")
        )
        async with client:
            provider = TrovaPrezziProvider(client)
            assert await provider.search(_catalog(), Market.IT) == []
