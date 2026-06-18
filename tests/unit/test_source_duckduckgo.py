"""Unit tests for the DuckDuckGo source provider (no network — MockTransport)."""

import httpx
import pytest

from repricing_engine.exceptions import SourceFetchError
from repricing_engine.models.enums import Market
from repricing_engine.models.product import CatalogProduct
from repricing_engine.sources.providers.duckduckgo import DuckDuckGoProvider


def _catalog() -> CatalogProduct:
    return CatalogProduct(
        sku="APL-IPH13-128",
        brand="Apple",
        title="Apple iPhone 13 128GB Blue",
        category="Smartphones",
        market=Market.IT,
    )


class TestDuckDuckGo:
    async def test_parses_and_decodes_redirects(self, mock_async_client, fixture_text):
        html = fixture_text("duckduckgo_results.html")
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["kl"] = request.url.params.get("kl", "")
            return httpx.Response(200, text=html)

        client = mock_async_client(handler)
        async with client:
            # rate limit 0 -> no sleeping in tests.
            provider = DuckDuckGoProvider(client, rate_limit_seconds=0.0)
            results = await provider.search(_catalog(), Market.IT)

        # IT market is localized via the region parameter.
        assert captured["kl"] == "it-it"
        urls = [r.url for r in results]
        assert "https://www.shopgamma.it/iphone-13-128" in urls
        assert "https://store.delta.it/apple/iphone13" in urls
        # No real DuckDuckGo redirect links leak through.
        assert all("duckduckgo.com/l/" not in u for u in urls)
        # DuckDuckGo self/ad links are filtered out.
        assert all("duckduckgo.com" not in (r.domain or "") for r in results)
        assert all(r.source_provider == "duckduckgo" for r in results)

    async def test_http_error_raises(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(429, text="rate limited"))
        async with client:
            provider = DuckDuckGoProvider(client, rate_limit_seconds=0.0)
            with pytest.raises(SourceFetchError):
                await provider.search(_catalog(), Market.IT)

    def test_resolve_url_handles_plain_and_redirect(self):
        redirect = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fx.it%2Fp&rut=abc"
        assert DuckDuckGoProvider._resolve_url(redirect) == "https://x.it/p"
        assert DuckDuckGoProvider._resolve_url("https://direct.it/p") == "https://direct.it/p"

    def test_is_available_toggle(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, text=""))
        assert DuckDuckGoProvider(client).is_available() is True
        assert DuckDuckGoProvider(client, enabled=False).is_available() is False
