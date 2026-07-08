"""Map a provider-agnostic :class:`RawSearchResult` to a :class:`CompetitorProduct`.

Identifiers are run through the same normalization/validation used by the CSV
ingestors, so a malformed EAN from a SERP snippet is dropped (``None``) rather
than producing a wrong exact-id match.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from repricing_engine.models.enums import Availability, Market, ShippingSource
from repricing_engine.models.product import CompetitorProduct
from repricing_engine.normalization.identifiers import normalize_ean, normalize_gtin, normalize_sku

if TYPE_CHECKING:
    from decimal import Decimal

    from repricing_engine.sources.models import RawSearchResult

# Currency assumed per market when a result carries none.
_MARKET_CURRENCY: dict[Market, str] = {
    Market.UK: "GBP",
    Market.US: "USD",
}
_DEFAULT_CURRENCY = "EUR"

# Providers whose shipping figure is a plain feed column vs. a structured API.
_FEED_PROVIDERS: frozenset[str] = frozenset({"feed"})
_API_PROVIDERS: frozenset[str] = frozenset({"ebay", "dataforseo", "serper", "keepa"})


def _shipping_source(provider: str, shipping_cost: Decimal | None) -> ShippingSource | None:
    """Derive the shipping provenance from the producing provider, if any.

    Only a result that actually carries a shipping cost gets a provenance stamp:
    ``FEED`` for a feed column, ``API`` for a structured marketplace API, else
    ``None`` (an unpriced SERP snippet has no shipping to attribute).
    """
    if shipping_cost is None:
        return None
    name = provider.lower()
    if name in _FEED_PROVIDERS:
        return ShippingSource.FEED
    if name in _API_PROVIDERS:
        return ShippingSource.API
    return None


def _domain(url: str) -> str:
    """Return the bare host (without a leading ``www.``) of a URL."""
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _source_id(provider: str, url: str) -> str:
    """A stable id for an offer, derived from its provider and URL."""
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]  # noqa: S324 (non-crypto id)
    return f"{provider}-{digest}"


def _normalized_sku(value: str | None) -> str | None:
    """Normalize an optional SKU, preserving ``None`` for absent values."""
    if not value:
        return None
    return normalize_sku(value)


def to_competitor_product(result: RawSearchResult, market: Market) -> CompetitorProduct:
    """Convert a raw search result into a normalized competitor product.

    Args:
        result: The provider-agnostic search result.
        market: The market this search was run for.

    Returns:
        A :class:`CompetitorProduct`. ``price`` may be ``None`` when the provider
        could not surface one (a later PDP fetch can fill it in).
    """
    currency = (result.currency or _MARKET_CURRENCY.get(market, _DEFAULT_CURRENCY)).strip().upper()
    return CompetitorProduct(
        source=result.source_provider,
        source_id=_source_id(result.source_provider, result.url),
        title=result.title.strip(),
        price=result.price,
        currency=currency,
        url=result.url,
        market=market,
        ean=normalize_ean(result.ean),
        gtin=normalize_gtin(result.ean),
        sku=_normalized_sku(result.sku),
        brand=result.brand,  # set only when confirmed against the catalog (see enrichment)
        shipping_cost=result.shipping_cost,
        shipping_source=_shipping_source(result.source_provider, result.shipping_cost),
        availability=Availability.UNKNOWN,
        seller=result.domain or _domain(result.url) or None,
        source_provider=result.source_provider,
        scraped_at=None,
        raw_data=dict(result.raw_data),
    )
