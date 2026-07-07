"""Unit tests for the DataForSEO source provider (no network — MockTransport)."""

import base64
import json
from decimal import Decimal

import httpx

from repricing_engine.models.enums import Market
from repricing_engine.models.product import CatalogProduct
from repricing_engine.sources.providers.dataforseo import DataForSeoProvider


def _catalog() -> CatalogProduct:
    return CatalogProduct(
        sku="GEB-SIG8",
        brand="Geberit",
        title="Geberit Sigma 8",
        category="Bathroom",
        market=Market.IT,
    )


def _item(**overrides) -> dict:
    item = {
        "title": "Geberit Sigma 8 flush plate",
        "price": 211.75,
        "currency": "EUR",
        "url": "https://www.example.it/p/1",
        "seller": "ShopIT",
        "gtin": "4006381333931",
    }
    item.update(overrides)
    return item


def _shopping_response(items: list[dict]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"status_code": 20000, "tasks": [{"result": [{"items": items}]}]},
    )


class TestDataForSeoSearch:
    async def test_shopping_returns_two_results_with_basic_auth(self, mock_async_client):
        captured: dict[str, str | None] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("Authorization")
            return _shopping_response(
                [
                    _item(url="https://www.a.it/1"),
                    _item(url="https://www.b.de/2"),
                ]
            )

        client = mock_async_client(handler)
        async with client:
            provider = DataForSeoProvider(client, "you@example.com", "api-pass")
            results = await provider.search(_catalog(), Market.IT)

        assert len(results) == 2
        assert all(r.source_provider == "dataforseo" for r in results)
        assert results[0].price == Decimal("211.75")
        assert results[0].currency == "EUR"
        assert results[0].domain == "a.it"
        expected = base64.b64encode(b"you@example.com:api-pass").decode("ascii")
        assert captured["auth"] == f"Basic {expected}"

    async def test_market_de_sets_location_and_language(self, mock_async_client):
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _shopping_response([])

        client = mock_async_client(handler)
        async with client:
            provider = DataForSeoProvider(client, "login", "pass")
            await provider.search(_catalog(), Market.DE)

        body = captured["body"]
        assert body[0]["location_code"] == 2276
        assert body[0]["language_code"] == "de"
        assert body[0]["keyword"]

    async def test_unmapped_market_makes_no_request(self, mock_async_client):
        called = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            called["n"] += 1
            return _shopping_response([])

        client = mock_async_client(handler)
        async with client:
            provider = DataForSeoProvider(client, "login", "pass")
            results = await provider.search(_catalog(), Market.NL)

        assert results == []
        assert called["n"] == 0

    async def test_transport_error_returns_empty(self, mock_async_client):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection reset")

        client = mock_async_client(handler)
        async with client:
            provider = DataForSeoProvider(client, "login", "pass")
            results = await provider.search(_catalog(), Market.IT)

        assert results == []

    async def test_parses_price_object_and_skips_item_without_url(self, mock_async_client):
        items = [
            _item(price={"current": 211.75}, url="https://www.a.it/1"),
            _item(url=None),  # missing url -> skipped
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return _shopping_response(items)

        client = mock_async_client(handler)
        async with client:
            provider = DataForSeoProvider(client, "login", "pass")
            results = await provider.search(_catalog(), Market.IT)

        assert len(results) == 1
        assert results[0].price == Decimal("211.75")


class TestDataForSeoAvailability:
    def test_is_available_requires_enabled_and_credentials(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, json={}))
        assert DataForSeoProvider(client, "login", "pass").is_available() is True
        assert DataForSeoProvider(client, "", "pass").is_available() is False
        assert DataForSeoProvider(client, "login", "").is_available() is False
        assert DataForSeoProvider(client, "login", "pass", enabled=False).is_available() is False
