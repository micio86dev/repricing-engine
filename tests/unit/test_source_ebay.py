"""Unit tests for the eBay Browse API source provider (no network — MockTransport)."""

from decimal import Decimal

import httpx

from repricing_engine.config import Settings
from repricing_engine.models.enums import Market
from repricing_engine.models.product import CatalogProduct
from repricing_engine.sources.orchestrator import SourceFetcher
from repricing_engine.sources.providers.ebay import EbaySourceProvider

_IDENTITY_ENDPOINT = "identity/v1/oauth2/token"


def _catalog_with_ean() -> CatalogProduct:
    return CatalogProduct(
        sku="APL-IPH13-128",
        ean="4006381333931",
        brand="Apple",
        title="Apple iPhone 13 128GB Blue",
        category="Smartphones",
        market=Market.IT,
    )


def _catalog_without_ean() -> CatalogProduct:
    return CatalogProduct(
        sku="APL-IPH13-128",
        brand="Apple",
        title="Apple iPhone 13 128GB Blue",
        category="Smartphones",
        market=Market.IT,
    )


def _token_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": "tok-123",
            "expires_in": 7200,
            "token_type": "Application Access Token",
        },
    )


def _item_summary(**overrides) -> dict:
    item = {
        "itemId": "v1|123|0",
        "title": "Apple iPhone 13 128GB Blue",
        "price": {"value": "211.75", "currency": "EUR"},
        "itemWebUrl": "https://www.ebay.it/itm/123",
        "seller": {"username": "topseller_it"},
        "shippingOptions": [{"shippingCost": {"value": "5.90", "currency": "EUR"}}],
        "gtin": "4006381333931",
    }
    item.update(overrides)
    return item


class TestEbaySearch:
    async def test_fetches_token_then_maps_results(self, mock_async_client):
        def handler(request: httpx.Request) -> httpx.Response:
            if _IDENTITY_ENDPOINT in str(request.url):
                return _token_response()
            return httpx.Response(200, json={"itemSummaries": [_item_summary()]})

        client = mock_async_client(handler)
        async with client:
            provider = EbaySourceProvider(client, "id", "secret")
            results = await provider.search(_catalog_without_ean(), Market.IT)

        assert len(results) == 1
        result = results[0]
        assert result.source_provider == "ebay"
        assert result.price == Decimal("211.75")
        assert result.shipping_cost == Decimal("5.90")
        assert result.currency == "EUR"
        assert result.url == "https://www.ebay.it/itm/123"
        assert result.domain == "ebay.it"
        assert result.ean == "4006381333931"

    async def test_free_shipping_is_zero(self, mock_async_client):
        item = _item_summary(
            shippingOptions=[{"shippingCost": {"value": "0.0", "currency": "EUR"}}]
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if _IDENTITY_ENDPOINT in str(request.url):
                return _token_response()
            return httpx.Response(200, json={"itemSummaries": [item]})

        client = mock_async_client(handler)
        async with client:
            provider = EbaySourceProvider(client, "id", "secret")
            results = await provider.search(_catalog_without_ean(), Market.IT)

        assert results[0].shipping_cost == Decimal("0")

    async def test_missing_shipping_options_yields_none(self, mock_async_client):
        item = _item_summary()
        del item["shippingOptions"]

        def handler(request: httpx.Request) -> httpx.Response:
            if _IDENTITY_ENDPOINT in str(request.url):
                return _token_response()
            return httpx.Response(200, json={"itemSummaries": [item]})

        client = mock_async_client(handler)
        async with client:
            provider = EbaySourceProvider(client, "id", "secret")
            results = await provider.search(_catalog_without_ean(), Market.IT)

        assert results[0].shipping_cost is None

    async def test_market_maps_to_marketplace_header(self, mock_async_client):
        captured: dict[str, str | None] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if _IDENTITY_ENDPOINT in str(request.url):
                return _token_response()
            captured["marketplace"] = request.headers.get("X-EBAY-C-MARKETPLACE-ID")
            return httpx.Response(200, json={"itemSummaries": []})

        client = mock_async_client(handler)
        async with client:
            provider = EbaySourceProvider(client, "id", "secret")
            await provider.search(_catalog_without_ean(), Market.DE)

        assert captured["marketplace"] == "EBAY_DE"

    async def test_valid_ean_uses_gtin_filter(self, mock_async_client):
        captured: dict[str, str | None] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if _IDENTITY_ENDPOINT in str(request.url):
                return _token_response()
            captured["filter"] = request.url.params.get("filter")
            captured["q"] = request.url.params.get("q")
            return httpx.Response(200, json={"itemSummaries": []})

        client = mock_async_client(handler)
        async with client:
            provider = EbaySourceProvider(client, "id", "secret")
            await provider.search(_catalog_with_ean(), Market.IT)

        assert captured["filter"] == "gtin:4006381333931"
        assert captured["q"] is None

    async def test_token_is_reused_across_searches(self, mock_async_client):
        counts = {"token": 0}
        auth_headers: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if _IDENTITY_ENDPOINT in str(request.url):
                counts["token"] += 1
                return _token_response()
            auth_headers.append(request.headers.get("Authorization"))
            return httpx.Response(200, json={"itemSummaries": []})

        client = mock_async_client(handler)
        async with client:
            provider = EbaySourceProvider(client, "id", "secret")
            await provider.search(_catalog_without_ean(), Market.IT)
            await provider.search(_catalog_without_ean(), Market.IT)

        assert counts["token"] == 1
        assert auth_headers == ["Bearer tok-123", "Bearer tok-123"]

    async def test_transport_error_on_search_returns_empty(self, mock_async_client):
        def handler(request: httpx.Request) -> httpx.Response:
            if _IDENTITY_ENDPOINT in str(request.url):
                return _token_response()
            raise httpx.ConnectError("connection reset")

        client = mock_async_client(handler)
        async with client:
            provider = EbaySourceProvider(client, "id", "secret")
            results = await provider.search(_catalog_without_ean(), Market.IT)

        assert results == []


class TestEbayAvailability:
    def test_available_requires_enabled_and_credentials(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, json={}))
        assert EbaySourceProvider(client, "id", "secret").is_available() is True
        assert EbaySourceProvider(client, "", "secret").is_available() is False
        assert EbaySourceProvider(client, "id", "").is_available() is False
        assert EbaySourceProvider(client, "id", "secret", enabled=False).is_available() is False

    def test_not_registered_without_credentials(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, json={}))
        settings = Settings(
            ebay_enabled=True,
            ebay_client_id=None,
            ebay_client_secret=None,
            groq_api_key=None,
        )
        fetcher = SourceFetcher.from_settings(settings, client)
        assert not any(p.name == "ebay" for p in fetcher.providers)

    def test_registered_with_credentials(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, json={}))
        settings = Settings(
            ebay_enabled=True,
            ebay_client_id="id",
            ebay_client_secret="secret",  # noqa: S106 (test credential, not a real secret)
            groq_api_key=None,
        )
        fetcher = SourceFetcher.from_settings(settings, client)
        ebay = [p for p in fetcher.providers if p.name == "ebay"]
        assert len(ebay) == 1
        assert ebay[0].cost_tier == 0

    async def test_unmapped_market_is_skipped(self, mock_async_client):
        called = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            called["n"] += 1
            return httpx.Response(200, json={})

        client = mock_async_client(handler)
        async with client:
            provider = EbaySourceProvider(client, "id", "secret")
            results = await provider.search(_catalog_without_ean(), Market.NL)

        assert results == []
        assert called["n"] == 0
