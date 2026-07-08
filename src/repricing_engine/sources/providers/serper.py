"""Serper.dev provider — cheapest Google SERP / Shopping discovery.

Discovers competitor offers by POSTing a keyword query to Serper.dev, the cheap
Google Search API. The ``"shopping"`` mode hits ``/shopping`` (Google Shopping
tiles, which already carry a price and a seller); the ``"search"`` mode hits
``/search`` (organic results). Authentication is a single ``X-API-KEY`` header,
so the provider is inert unless an API key is configured.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from repricing_engine.exceptions import SourceFetchError
from repricing_engine.models.enums import Market
from repricing_engine.normalization.price import parse_price_loose
from repricing_engine.sources.base import COST_CHEAP, BaseSourceProvider
from repricing_engine.sources.models import RawSearchResult
from repricing_engine.sources.query import brand_title_query

if TYPE_CHECKING:
    from repricing_engine.models.product import CatalogProduct

logger = logging.getLogger(__name__)

# Endpoint per mode. Any unknown mode falls back to Shopping (the priced tiles).
_ENDPOINTS: dict[str, str] = {
    "shopping": "https://google.serper.dev/shopping",
    "search": "https://google.serper.dev/search",
}
_DEFAULT_MODE = "shopping"

# Market -> (gl, hl): Google country code + interface language. Markets without a
# mapping here are skipped rather than queried with a wrong-country locale (which
# would poison a same-market price landscape).
_LOCALES: dict[Market, tuple[str, str]] = {
    Market.IT: ("it", "it"),
    Market.DE: ("de", "de"),
    Market.FR: ("fr", "fr"),
    Market.ES: ("es", "es"),
    Market.UK: ("gb", "en"),
    Market.US: ("us", "en"),
}

# Leading currency symbol -> ISO code, used to tag a price when Serper only gives
# a symbol-prefixed string ("$211.75"). No symbol => the EUR default is applied.
_CURRENCY_SYMBOLS: dict[str, str] = {"$": "USD", "£": "GBP", "€": "EUR"}

# The first number-like token in a delivery string ("€5.90 delivery" -> "5.90").
_AMOUNT_RE = re.compile(r"\d[\d.,]*")


class SerperProvider(BaseSourceProvider):
    """Discover competitor offers from the Serper.dev Google Search API."""

    name = "serper"
    cost_tier = COST_CHEAP

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        *,
        mode: str = "shopping",
        enabled: bool = True,
        timeout_seconds: float = 15.0,
    ) -> None:
        """Create the provider.

        Args:
            client: Injected async HTTP client (tests inject a MockTransport).
            api_key: Serper.dev API key, sent as the ``X-API-KEY`` header.
            mode: ``"shopping"`` (Google Shopping tiles) or ``"search"`` (organic);
                any other value falls back to ``"shopping"``.
            enabled: Master on/off switch from settings.
            timeout_seconds: Per-request timeout.
        """
        self._client = client
        self._api_key = api_key or ""
        self._mode = mode if mode in _ENDPOINTS else _DEFAULT_MODE
        self._endpoint = _ENDPOINTS[self._mode]
        self._enabled = enabled
        self._timeout = timeout_seconds

    def is_available(self) -> bool:
        """True only when enabled and an API key is configured."""
        if not self._enabled:
            return False
        if not self._api_key:
            logger.info("Serper disabled: set SERPER_API_KEY (get one at https://serper.dev).")
            return False
        return True

    async def search(
        self,
        product: CatalogProduct,
        market: Market,
    ) -> list[RawSearchResult]:
        """Search Serper for ``product`` in ``market``.

        Returns ``[]`` (never raises) when the market is unmapped, no query can be
        built, or any network/parse step fails — one failure must not sink the batch.
        """
        locale = _LOCALES.get(market)
        if locale is None:
            logger.info("Serper has no locale mapping for %s; skipping.", market)
            return []
        query = brand_title_query(product)
        if not query:
            return []
        gl, hl = locale
        try:
            payload = await self._fetch(query, gl, hl)
        except (SourceFetchError, httpx.HTTPError) as exc:
            logger.warning("Serper unavailable for %s: %s", product.sku, exc)
            return []
        return self._parse(payload)

    async def _fetch(self, query: str, gl: str, hl: str) -> dict[str, Any]:
        """POST the query to the configured endpoint and return the parsed JSON body."""
        headers = {"X-API-KEY": self._api_key, "Content-Type": "application/json"}
        body = {"q": query, "gl": gl, "hl": hl}
        try:
            response = await self._client.post(
                self._endpoint, headers=headers, json=body, timeout=self._timeout
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            msg = f"Serper {self._mode} search failed: {exc}"
            raise SourceFetchError(msg) from exc

    def _parse(self, payload: dict[str, Any]) -> list[RawSearchResult]:
        """Map a Serper Shopping payload's ``shopping`` list into raw results."""
        results: list[RawSearchResult] = []
        for entry in payload.get("shopping") or []:
            title = (entry.get("title") or "").strip()
            url = (entry.get("link") or "").strip()
            raw_price = entry.get("price")
            price = parse_price_loose(raw_price)
            if price is None:
                price = parse_price_loose(entry.get("priceValue"))
            if not title or not url or price is None:
                continue
            seller = (entry.get("source") or "").strip()
            results.append(
                RawSearchResult(
                    source_provider=self.name,
                    title=title,
                    url=url,
                    price=price,
                    currency=_currency_from_symbol(raw_price) or "EUR",
                    shipping_cost=_shipping_from_delivery(entry.get("delivery")),
                    domain=urlparse(url).netloc.lower().removeprefix("www."),
                    raw_data={"seller": seller},
                )
            )
        return results


def _currency_from_symbol(price: object) -> str | None:
    """Map a currency symbol in a price string to an ISO code, or ``None`` if absent."""
    haystack = price if isinstance(price, str) else ""
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in haystack:
            return code
    return None


def _shipping_from_delivery(delivery: object) -> Decimal | None:
    """Read a shipping cost from a Serper ``delivery`` string.

    A free-delivery promise ("Free delivery", "Consegna gratis") reads as
    ``Decimal("0")``; otherwise the first currency-tagged amount is parsed
    ("€5.90 delivery" -> ``Decimal("5.90")``). Returns ``None`` when the field is
    absent, empty, or carries no parseable amount.
    """
    text = delivery.strip() if isinstance(delivery, str) else ""
    if not text:
        return None
    lowered = text.lower()
    if "free" in lowered or "gratis" in lowered:
        return Decimal("0")
    match = _AMOUNT_RE.search(text)
    return parse_price_loose(match.group()) if match else None
