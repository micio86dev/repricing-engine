"""SearXNG provider — the primary (free, self-hosted) metasearch source.

Queries a SearXNG instance's JSON API (``GET {base}/search?format=json``) with
complementary strategies per product (quoted SKU, a valid EAN/GTIN, brand+title)
and paginates each over the first few result pages, so a single product surfaces
as many retailer URLs as the aggregated engines return. Requests are bounded
(concurrency + spacing) so large runs don't trip the upstream engines' CAPTCHAs.
If no base URL is configured the provider reports itself unavailable and logs a hint.
"""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from repricing_engine.exceptions import SourceFetchError
from repricing_engine.models.enums import Market
from repricing_engine.normalization.identifiers import normalize_ean, normalize_gtin
from repricing_engine.normalization.price import parse_price_loose
from repricing_engine.sources.base import COST_FREE, DEFAULT_USER_AGENT, BaseSourceProvider
from repricing_engine.sources.models import RawSearchResult
from repricing_engine.sources.query import brand_title_query
from repricing_engine.sources.snippet_price import extract_price_from_text

if TYPE_CHECKING:
    from decimal import Decimal

    from repricing_engine.models.product import CatalogProduct

logger = logging.getLogger(__name__)

# Market -> SearXNG language code (drives result locale).
_MARKET_LANGUAGE: dict[Market, str] = {
    Market.IT: "it-IT",
    Market.DE: "de-DE",
    Market.FR: "fr-FR",
    Market.ES: "es-ES",
    Market.NL: "nl-NL",
    Market.PT: "pt-PT",
    Market.BE: "fr-BE",
    Market.AT: "de-AT",
    Market.UK: "en-GB",
    Market.US: "en-US",
}


class SearXNGProvider(BaseSourceProvider):
    """Discover competitor offers via a SearXNG JSON endpoint."""

    name = "searxng"
    cost_tier = COST_FREE

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str | None,
        *,
        enabled: bool = True,
        timeout_seconds: float = 10.0,
        max_pages: int = 3,
        max_concurrency: int = 4,
        rate_limit_seconds: float = 0.0,
    ) -> None:
        """Create the provider.

        Args:
            client: Injected async HTTP client (tests inject a MockTransport).
            base_url: SearXNG instance base URL (e.g. ``http://localhost:8888``).
            enabled: Master on/off switch from settings.
            timeout_seconds: Per-request timeout.
            max_pages: How many result pages to fetch per query (>= 1).
            max_concurrency: Max in-flight SearXNG requests. Bounding this (instead
                of letting every product/query/page fire at once) keeps the upstream
                engines from tripping CAPTCHAs on large or repeated runs.
            rate_limit_seconds: Minimum spacing between SearXNG requests (politeness).
        """
        self._client = client
        self._base_url = (base_url or "").rstrip("/")
        self._enabled = enabled
        self._timeout = timeout_seconds
        self._max_pages = max(1, max_pages)
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._rate_limit = rate_limit_seconds
        self._lock = asyncio.Lock()
        self._last_request_at: float | None = None

    def is_available(self) -> bool:
        """True only when enabled and a base URL is configured."""
        if not self._enabled:
            return False
        if not self._base_url:
            logger.info(
                "SearXNG disabled: set SEARXNG_BASE_URL (e.g. run "
                "`docker compose up -d` and use http://localhost:8888)."
            )
            return False
        return True

    async def search(
        self,
        product: CatalogProduct,
        market: Market,
    ) -> list[RawSearchResult]:
        """Run every query strategy across pages and merge (URL-deduped) results."""
        results: list[RawSearchResult] = []
        seen_urls: set[str] = set()
        for query in self._build_queries(product):
            for page in range(1, self._max_pages + 1):
                page_results = await self._run_query(query, market, page)
                if not page_results:
                    break  # no more pages for this query
                for result in page_results:
                    if result.url not in seen_urls:
                        seen_urls.add(result.url)
                        results.append(result)
        return results

    def _build_queries(self, product: CatalogProduct) -> list[str]:
        """Build complementary query strings, most specific first.

        The catalog's EAN column is frequently corrupt, so identifier queries are
        emitted only for a *valid* EAN/GTIN; the distinctive SKU and the brand +
        title carry the search.
        """
        queries: list[str] = []
        brand = (product.brand or "").strip()

        if product.sku:
            queries.append(f'{brand} "{product.sku}"'.strip())

        valid_id = normalize_ean(product.ean) or normalize_gtin(product.gtin)
        if valid_id:
            queries.append(f'"{valid_id}"')

        broad = brand_title_query(product)
        if broad:
            queries.append(broad)

        # Preserve order, drop duplicates/empties.
        return list(dict.fromkeys(q for q in queries if q))

    async def _run_query(self, query: str, market: Market, page: int) -> list[RawSearchResult]:
        """Execute one SearXNG JSON query page (bounded concurrency + spacing)."""
        params = {
            "q": query,
            "format": "json",
            "language": _MARKET_LANGUAGE.get(market, "all"),
            "pageno": str(page),
        }
        headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
        async with self._semaphore:
            await self._space_requests()
            try:
                response = await self._client.get(
                    f"{self._base_url}/search",
                    params=params,
                    headers=headers,
                    timeout=self._timeout,
                )
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                msg = f"SearXNG query failed: {exc}"
                raise SourceFetchError(msg) from exc
        return self._parse(payload, market)

    async def _space_requests(self) -> None:
        """Enforce a minimum interval between SearXNG requests (politeness)."""
        if self._rate_limit <= 0:
            return
        async with self._lock:
            if self._last_request_at is not None:
                remaining = self._rate_limit - (monotonic() - self._last_request_at)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self._last_request_at = monotonic()

    def _parse(self, payload: dict[str, Any], market: Market) -> list[RawSearchResult]:
        """Map a SearXNG JSON payload into raw results."""
        results: list[RawSearchResult] = []
        for item in payload.get("results", []):
            url = (item.get("url") or "").strip()
            title = (item.get("title") or "").strip()
            if not url or not title:
                continue
            content = item.get("content") or None
            price = self._result_price(item, title, content, market)
            results.append(
                RawSearchResult(
                    source_provider=self.name,
                    title=title,
                    url=url,
                    snippet=content,
                    price=price,
                    currency="EUR" if price is not None else None,
                    domain=urlparse(url).netloc.lower().removeprefix("www."),
                    raw_data={"engine": item.get("engine", ""), "query": payload.get("query", "")},
                )
            )
        return results

    @staticmethod
    def _result_price(
        item: dict[str, Any], title: str, content: str | None, market: Market
    ) -> Decimal | None:
        """Prefer a structured price from the engine, else scan title/snippet."""
        structured = parse_price_loose(item.get("price"))
        if structured is not None:
            return structured
        return extract_price_from_text(" ".join(p for p in (title, content) if p), market)
