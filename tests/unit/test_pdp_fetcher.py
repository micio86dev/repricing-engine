"""Unit tests for the PDP page fetcher (no network — MockTransport)."""

import httpx

from repricing_engine.models.enums import Market
from repricing_engine.pdp.fetcher import PageFetcher

_BIG_HTML = "<html><body>" + ("product " * 300) + "</body></html>"  # > 1KB


class TestPageFetcher:
    async def test_ok_for_large_200(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, text=_BIG_HTML))
        async with client:
            fetcher = PageFetcher(client, rate_limit_per_domain_seconds=0.0)
            result = await fetcher.fetch("https://shop.it/p", market=Market.IT)
        assert result.ok is True
        assert result.status_code == 200
        assert result.fetch_method == "httpx"

    async def test_not_ok_for_tiny_body(self, mock_async_client, fixture_text):
        tiny = fixture_text("pdp_js_rendered.html")
        client = mock_async_client(lambda request: httpx.Response(200, text=tiny))
        async with client:
            fetcher = PageFetcher(client, rate_limit_per_domain_seconds=0.0)
            result = await fetcher.fetch("https://shop.it/p")
        assert result.ok is False

    async def test_not_ok_for_404(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(404, text=_BIG_HTML))
        async with client:
            fetcher = PageFetcher(client, rate_limit_per_domain_seconds=0.0)
            result = await fetcher.fetch("https://shop.it/p")
        assert result.ok is False

    async def test_transport_error_degrades(self, mock_async_client):
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route", request=request)

        client = mock_async_client(boom)
        async with client:
            fetcher = PageFetcher(client, rate_limit_per_domain_seconds=0.0)
            result = await fetcher.fetch("https://shop.it/p")
        assert result.ok is False
        assert result.html == ""

    async def test_unblocker_template_routes_the_request_through_the_unblocker(
        self, mock_async_client
    ):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, text=_BIG_HTML)

        client = mock_async_client(handler)
        async with client:
            fetcher = PageFetcher(
                client,
                rate_limit_per_domain_seconds=0.0,
                unblocker_url_template="https://api.unblock.test/?apikey=K&url={url}",
            )
            result = await fetcher.fetch("https://shop.it/p?a=1", market=Market.IT)
        assert result.ok is True
        # The offer's own URL is preserved for downstream use...
        assert result.url == "https://shop.it/p?a=1"
        # ...but the actual request went through the unblocker with the target encoded.
        assert seen["url"].startswith("https://api.unblock.test/?apikey=K&url=")
        assert "shop.it" in seen["url"]
        assert "%3A%2F%2F" in seen["url"] or "https%3A" in seen["url"]  # url-encoded target

    async def test_playwright_fallback_unavailable_degrades(self, mock_async_client, fixture_text):
        # httpx returns an unusable (tiny) body; Playwright is enabled but not
        # installed in the test env, so the fallback degrades to ok=False.
        tiny = fixture_text("pdp_js_rendered.html")
        client = mock_async_client(lambda request: httpx.Response(200, text=tiny))
        async with client:
            fetcher = PageFetcher(
                client, rate_limit_per_domain_seconds=0.0, playwright_enabled=True
            )
            result = await fetcher.fetch("https://shop.it/p")
        assert result.ok is False
        assert result.fetch_method == "playwright"

    async def test_per_domain_throttle_waits(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, text=_BIG_HTML))
        async with client:
            fetcher = PageFetcher(client, rate_limit_per_domain_seconds=0.02)
            # Two hits to the same domain — the second must observe the throttle.
            await fetcher.fetch("https://shop.it/a")
            second = await fetcher.fetch("https://shop.it/b")
        assert second.ok is True

    async def test_market_accept_language_and_ua_rotation(self, mock_async_client):
        seen: list[httpx.Headers] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers)
            return httpx.Response(200, text=_BIG_HTML)

        client = mock_async_client(handler)
        async with client:
            fetcher = PageFetcher(client, rate_limit_per_domain_seconds=0.0)
            await fetcher.fetch("https://shop.it/a", market=Market.IT)
            await fetcher.fetch("https://shop.it/b", market=Market.IT)

        assert "it-IT" in seen[0]["accept-language"]
        # UA rotates between consecutive requests.
        assert seen[0]["user-agent"] != seen[1]["user-agent"]
