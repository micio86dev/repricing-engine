"""PageFetcher — fetch a product page, cheapest method first.

Tier 1 is a plain ``httpx`` GET with a rotating realistic User-Agent and a
market-appropriate ``Accept-Language``. Tier 2 (optional, off by default) renders
the page with Playwright for JS-heavy sites; it is imported lazily so the
dependency is only required when ``PDP_PLAYWRIGHT_ENABLED=true``.

Fetches are globally bounded by a semaphore and throttled per domain.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from time import monotonic, perf_counter
from typing import TYPE_CHECKING
from urllib.parse import quote, urlparse

import httpx

from repricing_engine.models.enums import Market
from repricing_engine.pdp.models import FetchResult

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

# Minimum body size for an httpx response to count as usable HTML.
_MIN_HTML_BYTES = 1024

_USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
)
_MARKET_ACCEPT_LANGUAGE: dict[Market, str] = {
    Market.IT: "it-IT,it;q=0.9,en;q=0.5",
    Market.DE: "de-DE,de;q=0.9,en;q=0.5",
    Market.FR: "fr-FR,fr;q=0.9,en;q=0.5",
    Market.ES: "es-ES,es;q=0.9,en;q=0.5",
    Market.NL: "nl-NL,nl;q=0.9,en;q=0.5",
    Market.PT: "pt-PT,pt;q=0.9,en;q=0.5",
    Market.BE: "fr-BE,fr;q=0.9,nl;q=0.6,en;q=0.4",
    Market.AT: "de-AT,de;q=0.9,en;q=0.5",
    Market.UK: "en-GB,en;q=0.9",
    Market.US: "en-US,en;q=0.9",
}
_DEFAULT_ACCEPT_LANGUAGE = "en-US,en;q=0.9"


class PageFetcher:
    """Fetch product-page HTML with an httpx -> Playwright fallback cascade."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        timeout_seconds: float = 10.0,
        max_concurrent: int = 8,
        rate_limit_per_domain_seconds: float = 1.0,
        playwright_enabled: bool = False,
        unblocker_url_template: str | None = None,
    ) -> None:
        """Create the fetcher.

        Args:
            client: Injected async HTTP client (tests inject a MockTransport).
            timeout_seconds: Per-request timeout.
            max_concurrent: Global cap on simultaneous fetches.
            rate_limit_per_domain_seconds: Minimum delay between hits to a domain.
            playwright_enabled: Enable the Playwright JS-rendering fallback.
            unblocker_url_template: Optional anti-bot unblocker endpoint with a
                ``{url}`` placeholder (e.g. ZenRows/ScrapingBee/ScraperAPI). When
                set, the httpx GET is routed through it with the target URL
                url-encoded in place of ``{url}``; DataDome/Cloudflare-protected
                shops become readable without changing any provider code.
        """
        self._client = client
        self._timeout = timeout_seconds
        self._rate_limit = rate_limit_per_domain_seconds
        self._playwright_enabled = playwright_enabled
        self._unblocker_url_template = unblocker_url_template
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._domain_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._domain_last_at: dict[str, float] = {}
        self._ua_index = 0

    async def fetch(self, url: str, *, market: Market | None = None) -> FetchResult:
        """Fetch ``url``, trying httpx then (optionally) Playwright.

        Returns:
            A :class:`FetchResult`; ``ok`` is ``False`` when no usable HTML was
            retrieved (never raises for an ordinary fetch failure).
        """
        async with self._semaphore:
            await self._throttle(url)
            result = await self._fetch_httpx(url, market)
            if result.ok or not self._playwright_enabled:
                return result
            logger.debug("httpx fetch unusable for %s; trying Playwright.", url)
            return await self._fetch_playwright(url)

    async def _fetch_httpx(self, url: str, market: Market | None) -> FetchResult:
        """Tier 1: a plain httpx GET (optionally via an anti-bot unblocker)."""
        headers = self._headers(market)
        request_url = self._request_url(url)
        start = perf_counter()
        try:
            response = await self._client.get(
                request_url,
                headers=headers,
                timeout=self._timeout,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            logger.debug("httpx fetch failed for %s: %s", url, exc)
            return FetchResult(url=url, fetch_method="httpx", ok=False)
        elapsed_ms = (perf_counter() - start) * 1000.0
        html = response.text
        ok = response.status_code == httpx.codes.OK and len(html) >= _MIN_HTML_BYTES
        return FetchResult(
            url=url,
            html=html,
            status_code=response.status_code,
            fetch_method="httpx",
            response_time_ms=round(elapsed_ms, 3),
            ok=ok,
        )

    async def _fetch_playwright(self, url: str) -> FetchResult:
        """Tier 2: render with Playwright (lazy import; optional dependency)."""
        start = perf_counter()
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning(
                "PDP_PLAYWRIGHT_ENABLED is set but Playwright is not installed "
                "(`uv sync --extra playwright` then `playwright install chromium`)."
            )
            return FetchResult(url=url, fetch_method="playwright", ok=False)

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    await page.goto(url, timeout=self._timeout * 1000)
                    html = await page.content()
                finally:
                    await browser.close()
        except Exception as exc:
            logger.warning("Playwright fetch failed for %s: %s", url, exc)
            return FetchResult(url=url, fetch_method="playwright", ok=False)

        elapsed_ms = (perf_counter() - start) * 1000.0
        return FetchResult(
            url=url,
            html=html,
            status_code=httpx.codes.OK,
            fetch_method="playwright",
            response_time_ms=round(elapsed_ms, 3),
            ok=len(html) >= _MIN_HTML_BYTES,
        )

    def _request_url(self, url: str) -> str:
        """Route ``url`` through the configured unblocker endpoint, if any."""
        if not self._unblocker_url_template:
            return url
        return self._unblocker_url_template.replace("{url}", quote(url, safe=""))

    def _headers(self, market: Market | None) -> Mapping[str, str]:
        """Build request headers with a rotating UA + market Accept-Language."""
        user_agent = _USER_AGENTS[self._ua_index % len(_USER_AGENTS)]
        self._ua_index += 1
        accept_language = (
            _MARKET_ACCEPT_LANGUAGE.get(market, _DEFAULT_ACCEPT_LANGUAGE)
            if market is not None
            else _DEFAULT_ACCEPT_LANGUAGE
        )
        return {
            "User-Agent": user_agent,
            "Accept-Language": accept_language,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    async def _throttle(self, url: str) -> None:
        """Enforce the per-domain minimum interval before a request."""
        if self._rate_limit <= 0:
            return
        domain = urlparse(url).netloc.lower()
        async with self._domain_locks[domain]:
            last = self._domain_last_at.get(domain)
            if last is not None:
                remaining = self._rate_limit - (monotonic() - last)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._domain_last_at[domain] = monotonic()
