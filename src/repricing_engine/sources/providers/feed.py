"""Merchant product-feed provider — Google-Merchant-style RSS/XML product dumps.

Loads one or more configured product feeds (Awin, TrovaPrezzi, Idealo partner,
Google Merchant Center, ...) that publish offers as RSS 2.0 with the Google
Merchant ``g:`` namespace. Feeds carry price *and* shipping natively, so there is
no scraping and no anti-bot to defeat — the provider just filters the feed for
the catalog product.

Each feed is fetched and parsed exactly once — lazily on the first search and
cached in memory (guarded by an ``asyncio.Lock``, mirroring how the eBay provider
caches its OAuth token) — so subsequent per-product searches only re-filter the
in-memory offers. The provider is inert unless it is enabled and at least one
feed URL is configured.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

from repricing_engine.normalization.identifiers import normalize_ean, normalize_gtin
from repricing_engine.normalization.price import parse_price_loose
from repricing_engine.sources.base import COST_FREE, BaseSourceProvider
from repricing_engine.sources.models import RawSearchResult

if TYPE_CHECKING:
    from decimal import Decimal

    from repricing_engine.models.enums import Market
    from repricing_engine.models.product import CatalogProduct

logger = logging.getLogger(__name__)

# The Google Merchant product namespace. Feed elements such as ``<g:price>`` /
# ``<g:gtin>`` live here; standard RSS elements (``<title>``, ``<link>``) do not.
_G_NS = "http://base.google.com/ns/1.0"
_NS = {"g": _G_NS}
# Feeds state a currency word after the amount ("211.75 EUR"); when absent we
# default to euro, the currency of the markets this engine targets.
_DEFAULT_CURRENCY = "EUR"


class FeedProvider(BaseSourceProvider):
    """Discover competitor offers from configured Google-Merchant-style feeds."""

    name = "feed"
    cost_tier = COST_FREE

    def __init__(
        self,
        client: httpx.AsyncClient,
        feed_urls: list[str],
        *,
        enabled: bool = True,
        timeout_seconds: float = 20.0,
    ) -> None:
        """Create the provider.

        Args:
            client: Injected async HTTP client (tests inject a MockTransport).
            feed_urls: Product-feed URLs to load (comma-split from settings).
            enabled: Master on/off switch from settings.
            timeout_seconds: Per-feed fetch timeout.
        """
        self._client = client
        self._feed_urls = [url.strip() for url in feed_urls if url and url.strip()]
        self._enabled = enabled
        self._timeout = timeout_seconds
        self._load_lock = asyncio.Lock()
        self._offers: list[dict[str, Any]] | None = None

    def is_available(self) -> bool:
        """True only when enabled and at least one feed URL is configured."""
        if not self._enabled:
            return False
        if not self._feed_urls:
            logger.info(
                "Feed provider disabled: set FEED_URLS to a comma-separated list "
                "of Google-Merchant-style product-feed URLs."
            )
            return False
        return True

    async def search(
        self,
        product: CatalogProduct,
        market: Market,  # noqa: ARG002 (feeds are pre-localized; market is part of the contract)
    ) -> list[RawSearchResult]:
        """Return the cached feed offers that match ``product``.

        Loads (and caches) every configured feed on the first call, then filters
        the in-memory offers. An offer's GTIN equal to the product's *valid*
        EAN/GTIN is the primary match; a SKU-in-title hit or a full brand+title
        token match is the fallback. Never raises — a broken feed simply
        contributes nothing.
        """
        await self._ensure_loaded()
        valid_id = normalize_ean(product.ean) or normalize_gtin(product.gtin)
        return [
            self._to_result(offer)
            for offer in self._offers or []
            if _matches(offer, product, valid_id)
        ]

    async def _ensure_loaded(self) -> None:
        """Fetch + parse every feed exactly once, caching the parsed offers."""
        if self._offers is not None:
            return
        async with self._load_lock:
            if self._offers is not None:  # another coroutine won the load race
                return
            offers: list[dict[str, Any]] = []
            for url in self._feed_urls:
                offers.extend(await self._load_feed(url))
            self._offers = offers

    async def _load_feed(self, url: str) -> list[dict[str, Any]]:
        """Fetch and parse one feed; a failure logs a warning and yields ``[]``.

        A single bad feed (network error, non-2xx, or malformed XML) must never
        sink the others, so every failure degrades to an empty contribution.
        """
        try:
            response = await self._client.get(url, timeout=self._timeout)
            response.raise_for_status()
            # Parse from bytes so an XML encoding declaration is honoured (parsing a
            # str with a declaration raises). The feed URL is operator-configured.
            root = ET.fromstring(response.content)  # noqa: S314 (trusted, configured feed)
        except (httpx.HTTPError, ET.ParseError, ValueError) as exc:
            logger.warning("Feed %s unavailable: %s", url, exc)
            return []
        return _parse_items(root)

    def _to_result(self, offer: dict[str, Any]) -> RawSearchResult:
        """Map a parsed feed offer into a provider-agnostic raw result."""
        link = offer["link"]
        return RawSearchResult(
            source_provider=self.name,
            title=offer["title"],
            url=link,
            price=offer["price"],
            currency=offer["currency"] or _DEFAULT_CURRENCY,
            shipping_cost=offer["shipping_cost"],
            domain=urlparse(link).netloc.lower().removeprefix("www."),
            # The feed's raw GTIN; the mapper re-validates it downstream.
            ean=offer["gtin"],
            raw_data={},
        )


def _parse_items(root: ET.Element) -> list[dict[str, Any]]:
    """Extract one offer dict per ``<item>`` in a parsed feed tree."""
    offers: list[dict[str, Any]] = []
    for item in root.iter("item"):
        title = _text(item.find("title"))
        link = _text(item.find("link"))
        if not title or not link:
            continue
        price, currency = _parse_money(_text(item.find("g:price", _NS)))
        offers.append(
            {
                "title": title,
                "link": link,
                "price": price,
                "currency": currency,
                "shipping_cost": _shipping_cost(item),
                "gtin": _text(item.find("g:gtin", _NS)) or None,
                "availability": _text(item.find("g:availability", _NS)) or None,
            }
        )
    return offers


def _shipping_cost(item: ET.Element) -> Decimal | None:
    """Read a shipping cost from nested ``g:shipping/g:price`` or flat ``g:shipping_price``.

    ``"0"`` / ``"0.00"`` reads as ``Decimal("0")`` (free shipping); a missing
    shipping node yields ``None`` (shipping unknown).
    """
    shipping = item.find("g:shipping", _NS)
    text = (
        _text(shipping.find("g:price", _NS))
        if shipping is not None
        else _text(item.find("g:shipping_price", _NS))
    )
    price, _currency = _parse_money(text)
    return price


def _matches(offer: dict[str, Any], product: CatalogProduct, valid_id: str | None) -> bool:
    """Whether a feed offer describes the same product as ``product``.

    Primary: the offer's GTIN, once GS1-validated, equals the product's valid
    EAN/GTIN. Fallback: the product SKU appears in the offer title, or every
    brand+title token is present in it (all case-insensitive).
    """
    offer_id = normalize_ean(offer["gtin"]) or normalize_gtin(offer["gtin"])
    if valid_id and offer_id and valid_id == offer_id:
        return True
    title_lower = offer["title"].lower()
    sku = (product.sku or "").strip().lower()
    if sku and sku in title_lower:
        return True
    tokens = f"{product.brand} {product.title}".lower().split()
    return bool(tokens) and all(token in title_lower for token in tokens)


def _parse_money(text: str) -> tuple[Decimal | None, str | None]:
    """Split a ``"211.75 EUR"``-style money string into ``(amount, currency)``.

    ``parse_price_loose`` strips the currency word to read the amount; the
    currency is the first alphabetic token, if any. ``("", ...)`` yields
    ``(None, None)``.
    """
    if not text:
        return None, None
    currency = next((token for token in text.split() if token.isalpha()), None)
    return parse_price_loose(text), currency


def _text(element: ET.Element | None) -> str:
    """Return an element's stripped text, or ``""`` when absent/empty."""
    if element is None or element.text is None:
        return ""
    return element.text.strip()
