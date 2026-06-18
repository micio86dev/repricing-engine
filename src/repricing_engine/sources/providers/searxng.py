"""SearXNG provider — the primary (free, self-hosted) metasearch source.

Queries a SearXNG instance's JSON API (``GET {base}/search?format=json``) with
two queries per product: an identifier-oriented one (SKU/EAN, quoted) and a
broad brand+title one. If no base URL is configured the provider reports itself
unavailable and logs a one-line setup hint.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from repricing_engine.exceptions import SourceFetchError
from repricing_engine.models.enums import Market
from repricing_engine.sources.base import COST_FREE, DEFAULT_USER_AGENT, BaseSourceProvider
from repricing_engine.sources.models import RawSearchResult

if TYPE_CHECKING:
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
# Market -> ("price", "buy") query terms (locale data, not UI language).
_MARKET_QUERY_TERMS: dict[Market, tuple[str, str]] = {
    Market.IT: ("prezzo", "acquista"),
    Market.DE: ("preis", "kaufen"),
    Market.FR: ("prix", "acheter"),
    Market.ES: ("precio", "comprar"),
    Market.NL: ("prijs", "kopen"),
    Market.PT: ("preço", "comprar"),
}
_DEFAULT_QUERY_TERMS = ("price", "buy")


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
    ) -> None:
        """Create the provider.

        Args:
            client: Injected async HTTP client (tests inject a MockTransport).
            base_url: SearXNG instance base URL (e.g. ``http://localhost:8888``).
            enabled: Master on/off switch from settings.
            timeout_seconds: Per-request timeout.
        """
        self._client = client
        self._base_url = (base_url or "").rstrip("/")
        self._enabled = enabled
        self._timeout = timeout_seconds

    def is_available(self) -> bool:
        """True only when enabled and a base URL is configured."""
        if not self._enabled:
            return False
        if not self._base_url:
            logger.info(
                "SearXNG disabled: set SEARXNG_BASE_URL (e.g. run "
                "`docker run -p 8888:8080 searxng/searxng` and use http://localhost:8888)."
            )
            return False
        return True

    async def search(
        self,
        product: CatalogProduct,
        market: Market,
    ) -> list[RawSearchResult]:
        """Run the identifier + broad queries and merge their results."""
        results: list[RawSearchResult] = []
        seen_urls: set[str] = set()
        for query in self._build_queries(product, market):
            for result in await self._run_query(query, market):
                if result.url not in seen_urls:
                    seen_urls.add(result.url)
                    results.append(result)
        return results

    def _build_queries(self, product: CatalogProduct, market: Market) -> list[str]:
        """Build the (identifier-oriented, broad) query strings for a product."""
        price_term = _MARKET_QUERY_TERMS.get(market, _DEFAULT_QUERY_TERMS)[0]
        queries: list[str] = []

        identifiers = [f'"{value}"' for value in (product.sku, product.ean) if value]
        if identifiers:
            queries.append(f"{' OR '.join(identifiers)} {price_term}")

        broad = " ".join(part for part in (product.brand, product.title, price_term) if part)
        if broad:
            queries.append(broad)
        return queries

    async def _run_query(self, query: str, market: Market) -> list[RawSearchResult]:
        """Execute one SearXNG JSON query, returning parsed results."""
        params = {
            "q": query,
            "format": "json",
            "language": _MARKET_LANGUAGE.get(market, "all"),
        }
        headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}
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
        return self._parse(payload)

    def _parse(self, payload: dict[str, Any]) -> list[RawSearchResult]:
        """Map a SearXNG JSON payload into raw results."""
        results: list[RawSearchResult] = []
        for item in payload.get("results", []):
            url = (item.get("url") or "").strip()
            title = (item.get("title") or "").strip()
            if not url or not title:
                continue
            results.append(
                RawSearchResult(
                    source_provider=self.name,
                    title=title,
                    url=url,
                    snippet=item.get("content") or None,
                    domain=urlparse(url).netloc.lower().removeprefix("www."),
                    raw_data={"engine": item.get("engine", ""), "query": payload.get("query", "")},
                )
            )
        return results
