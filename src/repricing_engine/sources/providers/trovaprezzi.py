"""TrovaPrezzi provider — Italian price-comparison pages (IT market only).

Fetches a TrovaPrezzi listing for the product and parses its offer rows with
BeautifulSoup. The public markup changes over time, so selectors are best-effort
and kept as constants; when the offer table can't be parsed the provider logs a
warning and falls back to the shared Groq AI extractor (if one is injected),
yielding a single best-effort offer rather than nothing.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from repricing_engine.exceptions import NormalizationError, SourceFetchError
from repricing_engine.models.enums import Market
from repricing_engine.normalization.price import normalize_price
from repricing_engine.sources.base import (
    COST_FREE,
    DEFAULT_ACCEPT,
    DEFAULT_USER_AGENT,
    BaseSourceProvider,
)
from repricing_engine.sources.models import RawSearchResult

if TYPE_CHECKING:
    from repricing_engine.models.product import CatalogProduct
    from repricing_engine.pdp.extractor import AiExtractor

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.trovaprezzi.it"
_SEARCH_PATH = "/prezzi.aspx"
# Offer-row selectors (best-effort; tune against live markup as needed).
_OFFER_ROW_SELECTORS = ".listing_item, li.item, div.item, .item_offer, [data-listing-item]"
_MERCHANT_SELECTORS = ".item_merchant, .merchant_name, .merchant, .item_shop, [data-merchant]"
_PRICE_SELECTORS = ".item_price, .item_total_price, .price, .item_basic_price, [data-price]"
_SHIPPING_SELECTORS = ".item_delivery, .item_shipping, .delivery_price, .item_shipping_price"
_FREE_SHIPPING_TERMS: frozenset[str] = frozenset({"gratis", "gratuita", "gratuito", "free"})


class TrovaPrezziProvider(BaseSourceProvider):
    """Discover Italian competitor offers from TrovaPrezzi listings."""

    name = "trovaprezzi"
    cost_tier = COST_FREE

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        enabled: bool = True,
        timeout_seconds: float = 10.0,
        rate_limit_seconds: float = 3.0,
        ai_extractor: AiExtractor | None = None,
    ) -> None:
        """Create the provider.

        Args:
            client: Injected async HTTP client (tests inject a MockTransport).
            enabled: Master on/off switch from settings.
            timeout_seconds: Per-request timeout.
            rate_limit_seconds: Delay (in seconds) between requests to avoid 403.
            ai_extractor: Optional Groq extractor used when parsing the offer
                table fails (graceful, last-resort fallback).
        """
        self._client = client
        self._enabled = enabled
        self._timeout = timeout_seconds
        self._rate_limit = rate_limit_seconds
        self._ai_extractor = ai_extractor

    def is_available(self) -> bool:
        """True when enabled (no configuration required)."""
        return self._enabled

    async def search(
        self,
        product: CatalogProduct,
        market: Market,
    ) -> list[RawSearchResult]:
        """Fetch and parse a TrovaPrezzi listing (IT only; ``[]`` elsewhere)."""
        if market is not Market.IT:
            logger.info("TrovaPrezzi only supports the IT market; skipping for %s.", market)
            return []

        query = " ".join(part for part in (product.brand, product.title) if part).strip()
        if not query:
            return []

        url = f"{_BASE_URL}{_SEARCH_PATH}"
        html = await self._fetch(url, query)
        offers = self._parse_offers(html, url)
        if offers:
            return offers

        logger.warning("TrovaPrezzi: no offers parsed from %r; trying AI fallback.", query)
        return await self._ai_fallback(html, product, url)

    async def _fetch(self, url: str, query: str) -> str:
        """GET the listing page for ``query`` with retry logic and rate limiting."""
        await asyncio.sleep(self._rate_limit)  # Respect rate limiting

        # Build anti-bot headers (browser-like)
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": DEFAULT_ACCEPT,
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Referer": _BASE_URL,
            "Cache-Control": "max-age=0",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Microsoft Edge";v="120"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
        }

        max_retries = 4
        for attempt in range(max_retries):
            try:
                response = await self._client.get(
                    url,
                    params={"libt": "1", "query": query},
                    headers=headers,
                    timeout=self._timeout,
                )
                response.raise_for_status()
                return response.text
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 429 and attempt < max_retries - 1:
                    wait_time = 2**attempt  # exponential backoff: 1s, 2s, 4s, 8s
                    logger.debug(
                        "TrovaPrezzi 429 on attempt %d/%d; retrying after %ds",
                        attempt + 1,
                        max_retries,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    continue
                if status in (403, 404):
                    # Cloudflare block (403) or product-not-found (404): TrovaPrezzi is
                    # frequently bot-protected from server IPs. This is expected — degrade
                    # to "no offers" quietly (SearXNG surfaces trovaprezzi.it URLs anyway).
                    logger.info(
                        "TrovaPrezzi unavailable (HTTP %d) for %r; skipping.", status, query
                    )
                    return ""
                msg = f"TrovaPrezzi query failed: {exc}"
                raise SourceFetchError(msg) from exc
            except httpx.HTTPError as exc:
                msg = f"TrovaPrezzi query failed: {exc}"
                raise SourceFetchError(msg) from exc

        msg = f"TrovaPrezzi query failed after {max_retries} attempts"
        raise SourceFetchError(msg)

    def _parse_offers(self, html: str, page_url: str) -> list[RawSearchResult]:
        """Parse the offer rows from a TrovaPrezzi listing page."""
        soup = BeautifulSoup(html, "lxml")
        results: list[RawSearchResult] = []
        for row in soup.select(_OFFER_ROW_SELECTORS):
            link = row.select_one("a[href]")
            price_el = row.select_one(_PRICE_SELECTORS)
            if link is None or price_el is None:
                continue
            target = urljoin(page_url, str(link["href"]))
            merchant_el = row.select_one(_MERCHANT_SELECTORS)
            shipping_el = row.select_one(_SHIPPING_SELECTORS)
            merchant = merchant_el.get_text(strip=True) if merchant_el else ""
            shipping_text = shipping_el.get_text(strip=True) if shipping_el else None
            results.append(
                RawSearchResult(
                    source_provider=self.name,
                    title=link.get_text(strip=True) or merchant,
                    url=target,
                    price=_money(price_el.get_text(strip=True)),
                    currency="EUR",
                    shipping_cost=_shipping(shipping_text),
                    domain=urlparse(target).netloc.lower().removeprefix("www."),
                    raw_data={"merchant": merchant},
                )
            )
        return results

    async def _ai_fallback(
        self,
        html: str,
        product: CatalogProduct,
        url: str,
    ) -> list[RawSearchResult]:
        """Extract a single offer via the injected AI extractor, if available."""
        if self._ai_extractor is None:
            return []
        extraction = await self._ai_extractor.extract(html, product, url)
        if extraction is None or extraction.price is None:
            return []
        return [
            RawSearchResult(
                source_provider=self.name,
                title=product.title,
                url=url,
                price=extraction.price,
                currency=extraction.currency or "EUR",
                shipping_cost=extraction.shipping_cost,
                domain=urlparse(url).netloc.lower().removeprefix("www."),
                raw_data={"extraction": "ai"},
            )
        ]


def _money(text: str) -> Decimal | None:
    """Parse an Italian-formatted price string, or ``None`` if unparseable."""
    try:
        return normalize_price(text, "EUR", Market.IT)
    except NormalizationError:
        return None


def _shipping(text: str | None) -> Decimal | None:
    """Parse a shipping cost; free-shipping phrases map to ``0``."""
    if text is None:
        return None
    lowered = text.strip().lower()
    if not lowered:
        return None
    if any(term in lowered for term in _FREE_SHIPPING_TERMS):
        return Decimal("0")
    return _money(text)
