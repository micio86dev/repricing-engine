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

    def test_build_queries_uses_sku_valid_ean_and_title(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, json={}))
        provider = SearXNGProvider(client, base_url="http://localhost:8888")
        queries = provider._build_queries(_catalog())
        # The distinctive SKU is quoted (brand-prefixed) for precision.
        assert any('"APL-IPH13-128"' in q for q in queries)
        # A *valid* EAN is emitted as its own quoted query.
        assert any('"4006381333931"' in q for q in queries)
        # A brand + title broad query is present.
        assert any("Apple" in q and "iPhone" in q for q in queries)
        # No forced price term (it hurts recall).
        assert all("prezzo" not in q for q in queries)

    def test_build_queries_skips_corrupt_ean(self, mock_async_client):
        """An invalid/corrupt EAN (e.g. Excel scientific notation) is not queried."""
        client = mock_async_client(lambda request: httpx.Response(200, json={}))
        provider = SearXNGProvider(client, base_url="http://localhost:8888")
        corrupt = CatalogProduct(
            sku="AP19993",
            ean="8,02586E+12",  # Excel-mangled, fails checksum -> dropped
            brand="Fantini Cosmi",
            title="Fantini Cosmi ECOCOMFORT PLUS AP19993 160mm",
            category="",
            market=Market.IT,
        )
        queries = provider._build_queries(corrupt)
        assert any('"AP19993"' in q for q in queries)  # SKU still carries the search
        assert all("8" * 3 not in q for q in queries)  # no barcode-like query emitted

    async def test_paginates_until_empty_page(self, mock_async_client):
        """Each query is paginated; an empty page stops pagination for that query."""
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            page = request.url.params.get("pageno")
            calls.append(page)
            if page == "1":
                return httpx.Response(
                    200,
                    json={
                        "results": [{"url": "https://a.it/x", "title": "X", "content": "€ 10,00"}]
                    },
                )
            return httpx.Response(200, json={"results": []})  # page 2 empty -> stop

        client = mock_async_client(handler)
        async with client:
            provider = SearXNGProvider(client, base_url="http://localhost:8888", max_pages=5)
            results = await provider.search(_catalog(), Market.IT)

        assert "2" in calls  # went past page 1
        assert "3" not in calls  # stopped at the first empty page
        assert any(r.price is not None for r in results)  # snippet price parsed
