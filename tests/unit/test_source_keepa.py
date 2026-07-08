"""Unit tests for the Keepa (Amazon) source provider (no network — MockTransport)."""

from decimal import Decimal

import httpx

from repricing_engine.models.enums import Market
from repricing_engine.models.product import CatalogProduct
from repricing_engine.sources.providers.keepa import KeepaProvider

# A GS1-valid EAN-13 (matches the Keepa example in docs/INTEGRATIONS.md §2.8).
_VALID_EAN = "8025863058632"


def _catalog_with_ean() -> CatalogProduct:
    return CatalogProduct(
        sku="GEB-SIGMA8",
        ean=_VALID_EAN,
        brand="Geberit",
        title="Geberit Sigma8 Cistern",
        category="Bathroom",
        market=Market.IT,
    )


def _catalog_without_ean() -> CatalogProduct:
    return CatalogProduct(
        sku="GEB-SIGMA8",
        brand="Geberit",
        title="Geberit Sigma8 Cistern",
        category="Bathroom",
        market=Market.IT,
    )


def _product(**overrides) -> dict:
    product = {
        "asin": "B00ABCDEF0",
        "title": "Geberit Sigma8 Cistern",
        "stats": {"current": [21175, -1, -1]},
    }
    product.update(overrides)
    return product


class TestKeepaSearch:
    async def test_valid_ean_maps_amazon_offer(self, mock_async_client):
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"products": [_product()]})

        client = mock_async_client(handler)
        async with client:
            provider = KeepaProvider(client, "key-123")
            results = await provider.search(_catalog_with_ean(), Market.IT)

        assert len(results) == 1
        result = results[0]
        assert result.source_provider == "keepa"
        assert result.price == Decimal("211.75")  # 21175 cents -> 211.75
        assert result.shipping_cost is None
        assert result.currency == "EUR"
        assert "amazon.it/dp/B00ABCDEF0" in result.url
        assert result.domain == "amazon.it"
        assert result.ean == _VALID_EAN
        assert result.raw_data["seller"] == "Amazon"
        # The request carried the key, the Amazon.it domain id, and the EAN code.
        assert "key=key-123" in captured["url"]
        assert "domain=8" in captured["url"]
        assert f"code={_VALID_EAN}" in captured["url"]

    async def test_no_current_price_is_skipped(self, mock_async_client):
        product = _product(stats={"current": [-1]})

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"products": [product]})

        client = mock_async_client(handler)
        async with client:
            provider = KeepaProvider(client, "key-123")
            results = await provider.search(_catalog_with_ean(), Market.IT)

        assert results == []

    async def test_missing_identifier_makes_no_request(self, mock_async_client):
        called = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            called["n"] += 1
            return httpx.Response(200, json={"products": []})

        client = mock_async_client(handler)
        async with client:
            provider = KeepaProvider(client, "key-123")
            results = await provider.search(_catalog_without_ean(), Market.IT)

        assert results == []
        assert called["n"] == 0

    async def test_market_de_uses_domain_and_tld(self, mock_async_client):
        captured: dict[str, str] = {}
        product = _product(asin="B00XYZ0000", stats={"current": [9999]})

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"products": [product]})

        client = mock_async_client(handler)
        async with client:
            provider = KeepaProvider(client, "key-123")
            results = await provider.search(_catalog_with_ean(), Market.DE)

        assert "domain=3" in captured["url"]
        assert "amazon.de/dp/B00XYZ0000" in results[0].url
        assert results[0].currency == "EUR"

    async def test_transport_error_returns_empty(self, mock_async_client):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection reset")

        client = mock_async_client(handler)
        async with client:
            provider = KeepaProvider(client, "key-123")
            results = await provider.search(_catalog_with_ean(), Market.IT)

        assert results == []


class TestKeepaAvailability:
    def test_available_requires_enabled_and_key(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, json={}))
        assert KeepaProvider(client, "key-123").is_available() is True
        assert KeepaProvider(client, "").is_available() is False
        assert KeepaProvider(client, "key-123", enabled=False).is_available() is False
