"""DuckDuckGo provider — a free SERP fallback via the HTML endpoint.

Parses ``https://html.duckduckgo.com/html/`` (no API key, no JS). Self-throttled
with a per-instance minimum interval (``asyncio.Lock`` + monotonic clock) to stay
polite. Result links are DuckDuckGo redirects (``/l/?uddg=<encoded>``); the real
target URL is decoded from the ``uddg`` parameter.
"""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from repricing_engine.exceptions import SourceFetchError
from repricing_engine.models.enums import Market
from repricing_engine.sources.base import COST_FREE, DEFAULT_USER_AGENT, BaseSourceProvider
from repricing_engine.sources.models import RawSearchResult

if TYPE_CHECKING:
    from repricing_engine.models.product import CatalogProduct

logger = logging.getLogger(__name__)

_ENDPOINT = "https://html.duckduckgo.com/html/"

# Market -> DuckDuckGo region code (the ``kl`` parameter), so an IT query returns
# Italian retailers rather than US ones. ``wt-wt`` is DuckDuckGo's "no region".
_MARKET_REGION: dict[Market, str] = {
    Market.IT: "it-it",
    Market.DE: "de-de",
    Market.FR: "fr-fr",
    Market.ES: "es-es",
    Market.NL: "nl-nl",
    Market.PT: "pt-pt",
    Market.BE: "be-fr",
    Market.AT: "at-de",
    Market.UK: "uk-en",
    Market.US: "us-en",
}


class DuckDuckGoProvider(BaseSourceProvider):
    """Discover competitor offers by scraping DuckDuckGo's HTML results."""

    name = "duckduckgo"
    cost_tier = COST_FREE

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        enabled: bool = True,
        rate_limit_seconds: float = 2.0,
        timeout_seconds: float = 10.0,
    ) -> None:
        """Create the provider.

        Args:
            client: Injected async HTTP client (tests inject a MockTransport).
            enabled: Master on/off switch from settings.
            rate_limit_seconds: Minimum delay enforced between requests.
            timeout_seconds: Per-request timeout.
        """
        self._client = client
        self._enabled = enabled
        self._rate_limit = rate_limit_seconds
        self._timeout = timeout_seconds
        self._lock = asyncio.Lock()
        self._last_request_at: float | None = None

    def is_available(self) -> bool:
        """True when enabled (no configuration required)."""
        return self._enabled

    async def search(
        self,
        product: CatalogProduct,
        market: Market,
    ) -> list[RawSearchResult]:
        """Search DuckDuckGo for the product's brand + title."""
        query = " ".join(part for part in (product.brand, product.title) if part).strip()
        if not query:
            return []
        html = await self._fetch(query, market)
        return self._parse(html)

    async def _fetch(self, query: str, market: Market) -> str:
        """Fetch the HTML results page, honoring the per-instance rate limit."""
        async with self._lock:
            await self._wait_for_rate_limit()
            headers = {"User-Agent": DEFAULT_USER_AGENT}
            params = {"q": query, "kl": _MARKET_REGION.get(market, "wt-wt")}
            try:
                response = await self._client.get(
                    _ENDPOINT,
                    params=params,
                    headers=headers,
                    timeout=self._timeout,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                msg = f"DuckDuckGo query failed: {exc}"
                raise SourceFetchError(msg) from exc
            finally:
                self._last_request_at = monotonic()
            return response.text

    async def _wait_for_rate_limit(self) -> None:
        """Sleep just long enough to respect the minimum inter-request delay."""
        if self._rate_limit <= 0 or self._last_request_at is None:
            return
        elapsed = monotonic() - self._last_request_at
        remaining = self._rate_limit - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)

    def _parse(self, html: str) -> list[RawSearchResult]:
        """Extract organic results from a DuckDuckGo HTML page."""
        soup = BeautifulSoup(html, "lxml")
        results: list[RawSearchResult] = []
        for block in soup.select("div.result, div.web-result"):
            link = block.select_one("a.result__a")
            if link is None or not link.get("href"):
                continue
            target = self._resolve_url(str(link["href"]))
            if not target:
                continue
            domain = urlparse(target).netloc.lower().removeprefix("www.")
            if "duckduckgo.com" in domain:  # ad / related-search self-links
                continue
            snippet_el = block.select_one("a.result__snippet, div.result__snippet")
            results.append(
                RawSearchResult(
                    source_provider=self.name,
                    title=link.get_text(strip=True),
                    url=target,
                    snippet=snippet_el.get_text(strip=True) if snippet_el else None,
                    domain=domain,
                    raw_data={},
                )
            )
        return results

    @staticmethod
    def _resolve_url(href: str) -> str | None:
        """Decode DuckDuckGo's ``/l/?uddg=`` redirect to the real target URL."""
        if href.startswith("//"):
            href = f"https:{href}"
        parsed = urlparse(href)
        if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
            targets = parse_qs(parsed.query).get("uddg")
            return targets[0] if targets else None
        return href or None
