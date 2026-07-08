"""Keepa (Amazon) source provider — Amazon offers/prices by EAN/GTIN.

Looks up an Amazon product by its GS1 identifier via the Keepa Product API
(``GET https://api.keepa.com/product``) and reads the current Amazon price from
``stats.current[0]`` (an integer in *cents*; ``-1`` means no current price). Keepa
has no keyword search here, so a catalog product without a valid EAN/GTIN yields no
request. The provider is inert unless both ``enabled`` and an ``api_key`` are set.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import httpx

from repricing_engine.exceptions import SourceFetchError
from repricing_engine.models.enums import Market
from repricing_engine.normalization.identifiers import normalize_ean, normalize_gtin
from repricing_engine.sources.base import COST_CHEAP, BaseSourceProvider
from repricing_engine.sources.models import RawSearchResult

if TYPE_CHECKING:
    from repricing_engine.models.product import CatalogProduct

logger = logging.getLogger(__name__)

_API_URL = "https://api.keepa.com/product"
_DEFAULT_TIMEOUT_SECONDS = 20.0

# Market -> Amazon domain id understood by Keepa. Markets without an Amazon domain
# here are skipped rather than mapped to a wrong-country marketplace (which would
# poison a same-market price landscape).
_DOMAIN_IDS: dict[Market, int] = {
    Market.IT: 8,
    Market.DE: 3,
    Market.FR: 4,
    Market.ES: 9,
    Market.UK: 2,
    Market.US: 1,
}

# Market -> Amazon top-level domain, used to build the canonical ``/dp/<asin>`` URL.
_TLDS: dict[Market, str] = {
    Market.IT: "it",
    Market.DE: "de",
    Market.FR: "fr",
    Market.ES: "es",
    Market.UK: "co.uk",
    Market.US: "com",
}

# Market -> ISO currency of the returned price.
_CURRENCIES: dict[Market, str] = {
    Market.IT: "EUR",
    Market.DE: "EUR",
    Market.FR: "EUR",
    Market.ES: "EUR",
    Market.UK: "GBP",
    Market.US: "USD",
}


class KeepaProvider(BaseSourceProvider):
    """Discover Amazon offers/prices from the Keepa Product API (by EAN/GTIN)."""

    name = "keepa"
    cost_tier = COST_CHEAP

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        *,
        enabled: bool = True,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        domains: dict[Market, int] | None = None,
    ) -> None:
        """Create the provider.

        Args:
            client: Injected async HTTP client (tests inject a MockTransport).
            api_key: Keepa API key.
            enabled: Master on/off switch from settings.
            timeout_seconds: Per-request timeout.
            domains: Optional override of the ``Market`` -> Amazon-domain-id map.
        """
        self._client = client
        self._api_key = api_key or ""
        self._enabled = enabled
        self._timeout = timeout_seconds
        self._domains = domains or dict(_DOMAIN_IDS)

    def is_available(self) -> bool:
        """True only when enabled and an API key is configured."""
        if not self._enabled:
            return False
        if not self._api_key:
            logger.info("Keepa disabled: set KEEPA_API_KEY (subscribe at https://keepa.com/#!api).")
            return False
        return True

    async def search(
        self,
        product: CatalogProduct,
        market: Market,
    ) -> list[RawSearchResult]:
        """Look up ``product`` on the Amazon marketplace for ``market``.

        Returns ``[]`` (never raises) when the market has no Amazon domain, the
        product has no valid EAN/GTIN (Keepa can't search by keyword here), or any
        network/parse step fails — one failure must not sink the batch.
        """
        domain = self._domains.get(market)
        if domain is None:
            logger.info("Keepa has no Amazon domain mapping for %s; skipping.", market)
            return []
        code = normalize_ean(product.ean) or normalize_gtin(product.gtin)
        if not code:
            logger.info(
                "Keepa needs a valid EAN/GTIN to look up %s; skipping (no keyword search).",
                product.sku,
            )
            return []
        try:
            payload = await self._fetch(domain, code)
        except (SourceFetchError, httpx.HTTPError) as exc:
            logger.warning("Keepa unavailable for %s: %s", product.sku, exc)
            return []
        return self._parse(payload, market, code)

    async def _fetch(self, domain: int, code: str) -> dict[str, Any]:
        """GET the Keepa product lookup and return the parsed JSON body."""
        params = {
            "key": self._api_key,
            "domain": str(domain),
            "code": code,
            "stats": "1",
        }
        try:
            response = await self._client.get(_API_URL, params=params, timeout=self._timeout)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            msg = f"Keepa product lookup failed: {exc}"
            raise SourceFetchError(msg) from exc

    def _parse(self, payload: dict[str, Any], market: Market, code: str) -> list[RawSearchResult]:
        """Map a Keepa ``products`` payload into raw results (priced offers only)."""
        tld = _TLDS[market]
        currency = _CURRENCIES[market]
        results: list[RawSearchResult] = []
        for product in payload.get("products") or []:
            asin = (product.get("asin") or "").strip()
            title = (product.get("title") or "").strip()
            if not asin or not title:
                continue
            cents = _current_price_cents(product)
            if cents is None or cents <= 0:
                continue
            results.append(
                RawSearchResult(
                    source_provider=self.name,
                    title=title,
                    url=f"https://www.amazon.{tld}/dp/{asin}",
                    price=Decimal(cents) / 100,
                    currency=currency,
                    # Keepa does not expose a shipping cost — leave it unknown.
                    shipping_cost=None,
                    domain=f"amazon.{tld}",
                    ean=code,
                    raw_data={"asin": asin, "seller": "Amazon"},
                )
            )
        return results


def _current_price_cents(product: dict[str, Any]) -> int | None:
    """Read ``stats.current[0]`` (Amazon price in cents), or ``None`` if absent."""
    stats = product.get("stats")
    if not isinstance(stats, dict):
        return None
    current = stats.get("current")
    if not isinstance(current, list) or not current:
        return None
    try:
        return int(current[0])
    except (TypeError, ValueError):
        return None
