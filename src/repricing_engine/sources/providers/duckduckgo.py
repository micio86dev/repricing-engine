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
from repricing_engine.sources.base import (
    COST_FREE,
    DEFAULT_ACCEPT,
    DEFAULT_USER_AGENT,
    BaseSourceProvider,
)
from repricing_engine.sources.models import RawSearchResult
from repricing_engine.sources.snippet_price import extract_price_from_text

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
        timeout_seconds: float = 5.0,
    ) -> None:
        """Create the provider.

        Args:
            client: Injected async HTTP client (tests inject a MockTransport).
            enabled: Master on/off switch from settings.
            rate_limit_seconds: Minimum delay enforced between requests.
            timeout_seconds: Per-request timeout (default 5s; DuckDuckGo may be
                unreachable on some networks).
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
        """Search DuckDuckGo with a brand+title query and a distinctive-SKU query."""
        results: list[RawSearchResult] = []
        seen_urls: set[str] = set()
        for query in self._build_queries(product):
            html = await self._fetch(query, market)
            for result in self._parse(html, market):
                if result.url not in seen_urls:
                    seen_urls.add(result.url)
                    results.append(result)
        return results

    @staticmethod
    def _build_queries(product: CatalogProduct) -> list[str]:
        """Build the brand+title and quoted-SKU queries (order-stable, deduped)."""
        brand = (product.brand or "").strip()
        queries: list[str] = []
        broad = " ".join(part for part in (brand, product.title) if part).strip()
        if broad:
            queries.append(broad)
        if product.sku:
            queries.append(f'{brand} "{product.sku}"'.strip())
        return list(dict.fromkeys(q for q in queries if q))

    async def _fetch(self, query: str, market: Market) -> str:
        """Fetch the HTML results page with retry logic and rate limiting."""
        async with self._lock:
            await self._wait_for_rate_limit()

            # Build anti-bot headers
            headers = {
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept": DEFAULT_ACCEPT,
                "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Referer": "https://html.duckduckgo.com/",
                "Cache-Control": "max-age=0",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            }

            params = {"q": query, "kl": _MARKET_REGION.get(market, "wt-wt")}

            max_retries = 2  # Minimal retries due to potential network blocking
            try:
                for attempt in range(max_retries):
                    try:
                        # Use separate connect timeout to fail fast on unreachable hosts
                        timeout = httpx.Timeout(self._timeout, connect=2.0)
                        response = await self._client.get(
                            _ENDPOINT,
                            params=params,
                            headers=headers,
                            timeout=timeout,
                        )
                        response.raise_for_status()
                        return response.text
                    except httpx.HTTPStatusError as exc:
                        status = exc.response.status_code
                        if status in (403, 429) and attempt < max_retries - 1:
                            wait_time = (
                                2**attempt
                            )  # exponential backoff (1s, 2s, ... over max_retries)
                            logger.debug(
                                "DuckDuckGo %d on attempt %d/%d; retrying after %ds",
                                status,
                                attempt + 1,
                                max_retries,
                                wait_time,
                            )
                            await asyncio.sleep(wait_time)
                            continue
                        msg = f"DuckDuckGo query failed: {exc}"
                        raise SourceFetchError(msg) from exc
                    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                        if attempt < max_retries - 1:
                            wait_time = (2**attempt) + 1  # backoff (2s, 3s, ... over max_retries)
                            logger.debug(
                                "DuckDuckGo connection error on attempt %d/%d; retrying after %ds",
                                attempt + 1,
                                max_retries,
                                wait_time,
                            )
                            await asyncio.sleep(wait_time)
                            continue
                        msg = f"DuckDuckGo connection failed: {exc}"
                        raise SourceFetchError(msg) from exc
                    except httpx.HTTPError as exc:
                        msg = f"DuckDuckGo query failed: {exc}"
                        raise SourceFetchError(msg) from exc

                msg = f"DuckDuckGo query failed after {max_retries} attempts"
                raise SourceFetchError(msg)
            finally:
                self._last_request_at = monotonic()

    async def _wait_for_rate_limit(self) -> None:
        """Sleep just long enough to respect the minimum inter-request delay."""
        if self._rate_limit <= 0 or self._last_request_at is None:
            return
        elapsed = monotonic() - self._last_request_at
        remaining = self._rate_limit - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)

    def _parse(self, html: str, market: Market) -> list[RawSearchResult]:
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
            title = link.get_text(strip=True)
            snippet = snippet_el.get_text(strip=True) if snippet_el else None
            price = extract_price_from_text(" ".join(p for p in (title, snippet) if p), market)
            results.append(
                RawSearchResult(
                    source_provider=self.name,
                    title=title,
                    url=target,
                    snippet=snippet,
                    price=price,
                    currency="EUR" if price is not None else None,
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
