"""Unit tests for the SearXNG source provider (no network — MockTransport)."""

import json

import httpx
import pytest

from repricing_engine.exceptions import SourceFetchError
from repricing_engine.models.enums import Market
from repricing_engine.models.product import CatalogProduct
from repricing_engine.sources.providers.searxng import SearXNGProvider


def _catalog() -> CatalogProduct:
    return CatalogProduct(
        sku="APL-IPH13-128",
        ean="4006381333931",
        brand="Apple",
        title="Apple iPhone 13 128GB Blue",
        category="Smartphones",
        market=Market.IT,
    )


class TestSearXNGAvailability:
    def test_unavailable_without_base_url(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, json={}))
        provider = SearXNGProvider(client, base_url=None)
        assert provider.is_available() is False

    def test_unavailable_when_disabled(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, json={}))
        provider = SearXNGProvider(client, base_url="http://localhost:8888", enabled=False)
        assert provider.is_available() is False

    def test_available_with_base_url(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, json={}))
        provider = SearXNGProvider(client, base_url="http://localhost:8888")
        assert provider.is_available() is True


class TestSearXNGSearch:
    async def test_parses_results(self, mock_async_client, fixture_text):
        payload = json.loads(fixture_text("searxng_response.json"))

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["format"] == "json"
            return httpx.Response(200, json=payload)

        client = mock_async_client(handler)
        async with client:
            provider = SearXNGProvider(client, base_url="http://localhost:8888")
            results = await provider.search(_catalog(), Market.IT)

        # Two valid results (the empty-URL entry is dropped), deduped across queries.
        assert len(results) == 2
        urls = {r.url for r in results}
        assert "https://www.shopalpha.it/apple-iphone-13-128gb-blue" in urls
        assert all(r.source_provider == "searxng" for r in results)
        assert all(r.domain and not r.domain.startswith("www.") for r in results)

    async def test_http_error_raises_source_fetch_error(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(502, text="bad gateway"))
        async with client:
            provider = SearXNGProvider(client, base_url="http://localhost:8888")
            with pytest.raises(SourceFetchError):
                await provider.search(_catalog(), Market.IT)

    def test_build_queries_uses_identifiers_and_broad(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, json={}))
        provider = SearXNGProvider(client, base_url="http://localhost:8888")
        queries = provider._build_queries(_catalog(), Market.IT)
        assert len(queries) == 2
        assert '"APL-IPH13-128"' in queries[0]
        assert '"4006381333931"' in queries[0]
        assert "prezzo" in queries[0]
        assert "Apple" in queries[1]
