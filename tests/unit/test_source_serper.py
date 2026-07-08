"""Unit tests for the Serper.dev SERP/Shopping provider (no network — MockTransport)."""

import json
from decimal import Decimal

import httpx
import pytest

from repricing_engine.models.enums import Market
from repricing_engine.models.product import CatalogProduct
from repricing_engine.sources.providers.serper import SerperProvider


def _catalog() -> CatalogProduct:
    return CatalogProduct(
        sku="APL-IPH13-128",
        brand="Apple",
        title="Apple iPhone 13 128GB Blue",
        category="Smartphones",
        market=Market.IT,
    )


def _shopping_entry(**overrides) -> dict:
    entry = {
        "title": "Apple iPhone 13 128GB Blue",
        "link": "https://www.example.it/p/iphone-13",
        "price": "€211,75",
        "priceValue": 211.75,
        "source": "ShopExample",
        "delivery": "Free delivery",
    }
    entry.update(overrides)
    return entry


class TestSerperSearch:
    async def test_maps_shopping_results_and_sends_api_key(self, mock_async_client):
        captured: dict[str, str | None] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["api_key"] = request.headers.get("X-API-KEY")
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"shopping": [_shopping_entry()]})

        client = mock_async_client(handler)
        async with client:
            provider = SerperProvider(client, "serper-key")
            results = await provider.search(_catalog(), Market.IT)

        assert captured["api_key"] == "serper-key"
        assert "google.serper.dev/shopping" in captured["url"]
        assert len(results) == 1
        result = results[0]
        assert result.source_provider == "serper"
        assert result.price == Decimal("211.75")
        assert result.currency == "EUR"
        assert result.url == "https://www.example.it/p/iphone-13"
        assert result.domain == "example.it"
        assert result.raw_data["seller"] == "ShopExample"

    @pytest.mark.parametrize(
        ("delivery", "expected"),
        [
            ("Free delivery", Decimal("0")),
            ("€5.90 delivery", Decimal("5.90")),
            (None, None),
        ],
    )
    async def test_delivery_maps_to_shipping_cost(self, mock_async_client, delivery, expected):
        entry = _shopping_entry()
        if delivery is None:
            entry.pop("delivery", None)
        else:
            entry["delivery"] = delivery

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"shopping": [entry]})

        client = mock_async_client(handler)
        async with client:
            provider = SerperProvider(client, "serper-key")
            results = await provider.search(_catalog(), Market.IT)

        assert results[0].shipping_cost == expected

    async def test_market_maps_to_gl_and_hl(self, mock_async_client):
        captured: dict[str, dict] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"shopping": []})

        client = mock_async_client(handler)
        async with client:
            provider = SerperProvider(client, "serper-key")
            await provider.search(_catalog(), Market.FR)

        assert captured["body"]["gl"] == "fr"
        assert captured["body"]["hl"] == "fr"

    async def test_unmapped_market_is_skipped(self, mock_async_client):
        called = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            called["n"] += 1
            return httpx.Response(200, json={})

        client = mock_async_client(handler)
        async with client:
            provider = SerperProvider(client, "serper-key")
            results = await provider.search(_catalog(), Market.NL)

        assert results == []
        assert called["n"] == 0

    async def test_transport_error_returns_empty(self, mock_async_client):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection reset")

        client = mock_async_client(handler)
        async with client:
            provider = SerperProvider(client, "serper-key")
            results = await provider.search(_catalog(), Market.IT)

        assert results == []


class TestSerperAvailability:
    def test_available_requires_enabled_and_key(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, json={}))
        assert SerperProvider(client, "serper-key").is_available() is True
        assert SerperProvider(client, "").is_available() is False
        assert SerperProvider(client, "serper-key", enabled=False).is_available() is False
