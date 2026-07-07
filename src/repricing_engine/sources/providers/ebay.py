"""eBay Browse API provider — official, free-tier new-item offers.

Discovers competitor offers from live eBay listings via the Browse API
(``GET /buy/browse/v1/item_summary/search``). Authentication uses the OAuth2
client-credentials grant: the application token is fetched once and cached in
memory (a monotonic clock tracks its expiry) so repeated searches reuse it until
it is close to expiring, instead of paying for a token exchange per query. The
provider is inert unless both a ``client_id`` and a ``client_secret`` are set.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from time import monotonic
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from repricing_engine.exceptions import SourceFetchError
from repricing_engine.models.enums import Market
from repricing_engine.normalization.identifiers import normalize_ean, normalize_gtin
from repricing_engine.normalization.price import parse_price_loose
from repricing_engine.sources.base import COST_FREE, BaseSourceProvider
from repricing_engine.sources.models import RawSearchResult

if TYPE_CHECKING:
    from decimal import Decimal

    from repricing_engine.models.product import CatalogProduct

logger = logging.getLogger(__name__)

_OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
_BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
_OAUTH_SCOPE = "https://api.ebay.com/oauth/api_scope"
_DEFAULT_TOKEN_TTL_SECONDS = 7200.0
# Refresh the cached token a minute before its stated expiry, so an in-flight
# search never races a token that expires mid-request.
_TOKEN_EXPIRY_SAFETY_SECONDS = 60.0
_DEFAULT_RESULT_LIMIT = 10

# Market -> eBay marketplace id (drives the ``X-EBAY-C-MARKETPLACE-ID`` header and
# the locale of the returned offers). Markets without a dedicated eBay marketplace
# here are skipped rather than mapped to a wrong-country site (which would poison a
# same-market price landscape).
_MARKETPLACE_IDS: dict[Market, str] = {
    Market.IT: "EBAY_IT",
    Market.DE: "EBAY_DE",
    Market.FR: "EBAY_FR",
    Market.ES: "EBAY_ES",
    Market.UK: "EBAY_GB",
    Market.US: "EBAY_US",
}


class EbaySourceProvider(BaseSourceProvider):
    """Discover competitor offers from the official eBay Browse API."""

    name = "ebay"
    cost_tier = COST_FREE

    def __init__(
        self,
        client: httpx.AsyncClient,
        client_id: str,
        client_secret: str,
        *,
        enabled: bool = True,
        timeout_seconds: float = 10.0,
        result_limit: int = _DEFAULT_RESULT_LIMIT,
        marketplaces: dict[Market, str] | None = None,
    ) -> None:
        """Create the provider.

        Args:
            client: Injected async HTTP client (tests inject a MockTransport).
            client_id: eBay App ID / OAuth client id.
            client_secret: eBay Cert ID / OAuth client secret.
            enabled: Master on/off switch from settings.
            timeout_seconds: Per-request timeout.
            result_limit: Max item summaries requested per search.
            marketplaces: Optional override of the ``Market`` -> marketplace-id map.
        """
        self._client = client
        self._client_id = client_id or ""
        self._client_secret = client_secret or ""
        self._enabled = enabled
        self._timeout = timeout_seconds
        self._result_limit = max(1, result_limit)
        self._marketplaces = marketplaces or dict(_MARKETPLACE_IDS)
        self._token_lock = asyncio.Lock()
        self._token: str | None = None
        self._token_expiry: float = 0.0

    def is_available(self) -> bool:
        """True only when enabled and both client credentials are configured."""
        if not self._enabled:
            return False
        if not (self._client_id and self._client_secret):
            logger.info(
                "eBay disabled: set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET "
                "(create a Production keyset at https://developer.ebay.com)."
            )
            return False
        return True

    async def search(
        self,
        product: CatalogProduct,
        market: Market,
    ) -> list[RawSearchResult]:
        """Search the Browse API for ``product`` in ``market``.

        Returns ``[]`` (never raises) when the market is unmapped, no query can be
        built, or any network/parse step fails — one failure must not sink the batch.
        """
        marketplace = self._marketplaces.get(market)
        if marketplace is None:
            logger.info("eBay has no marketplace mapping for %s; skipping.", market)
            return []
        params = self._build_params(product)
        if params is None:
            return []
        try:
            token = await self._access_token()
            payload = await self._fetch_items(params, marketplace, token)
        except (SourceFetchError, httpx.HTTPError) as exc:
            logger.warning("eBay unavailable for %s: %s", product.sku, exc)
            return []
        return self._parse(payload)

    def _build_params(self, product: CatalogProduct) -> dict[str, str] | None:
        """Build the Browse search params.

        A *valid* EAN/GTIN drives a precise ``filter=gtin:<id>`` query; otherwise the
        brand + title (falling back to the SKU) becomes the ``q`` keyword query. The
        catalog EAN column is frequently corrupt, so an identifier is used only after
        it passes GS1 validation. Returns ``None`` when no usable query exists.
        """
        limit = str(self._result_limit)
        valid_id = normalize_ean(product.ean) or normalize_gtin(product.gtin)
        if valid_id:
            return {"filter": f"gtin:{valid_id}", "limit": limit}
        brand = (product.brand or "").strip()
        keywords = " ".join(part for part in (brand, product.title) if part).strip()
        query = keywords or (product.sku or "").strip()
        if not query:
            return None
        return {"q": query, "limit": limit}

    async def _access_token(self) -> str:
        """Return the cached application token, fetching a fresh one when expired."""
        async with self._token_lock:
            if self._token is not None and monotonic() < self._token_expiry:
                return self._token
            payload = await self._request_token()
            token = payload.get("access_token")
            if not token:
                msg = "eBay OAuth response did not contain an access_token."
                raise SourceFetchError(msg)
            ttl = _coerce_ttl(payload.get("expires_in"))
            self._token = token
            self._token_expiry = monotonic() + max(0.0, ttl - _TOKEN_EXPIRY_SAFETY_SECONDS)
            return token

    async def _request_token(self) -> dict[str, Any]:
        """POST the client-credentials grant (HTTP Basic auth) and parse the body."""
        credentials = f"{self._client_id}:{self._client_secret}".encode()
        basic = base64.b64encode(credentials).decode("ascii")
        headers = {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        data = {"grant_type": "client_credentials", "scope": _OAUTH_SCOPE}
        try:
            response = await self._client.post(
                _OAUTH_URL, headers=headers, data=data, timeout=self._timeout
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            msg = f"eBay OAuth token request failed: {exc}"
            raise SourceFetchError(msg) from exc

    async def _fetch_items(
        self, params: dict[str, str], marketplace: str, token: str
    ) -> dict[str, Any]:
        """GET the Browse item-summary search and return the parsed JSON body."""
        headers = {
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": marketplace,
            "Accept": "application/json",
        }
        try:
            response = await self._client.get(
                _BROWSE_URL, params=params, headers=headers, timeout=self._timeout
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            msg = f"eBay Browse search failed: {exc}"
            raise SourceFetchError(msg) from exc

    def _parse(self, payload: dict[str, Any]) -> list[RawSearchResult]:
        """Map an eBay Browse payload's ``itemSummaries`` into raw results."""
        results: list[RawSearchResult] = []
        for item in payload.get("itemSummaries") or []:
            title = (item.get("title") or "").strip()
            url = (item.get("itemWebUrl") or "").strip()
            if not title or not url:
                continue
            price_obj = item.get("price") or {}
            price = parse_price_loose(price_obj.get("value"))
            currency = (price_obj.get("currency") or "").strip() or None
            seller = (item.get("seller") or {}).get("username") or ""
            results.append(
                RawSearchResult(
                    source_provider=self.name,
                    title=title,
                    url=url,
                    price=price,
                    currency=currency,
                    shipping_cost=_shipping_cost(item),
                    domain=urlparse(url).netloc.lower().removeprefix("www."),
                    # eBay may echo the item's GTIN; the mapper re-validates it.
                    ean=(item.get("gtin") or "").strip() or None,
                    raw_data={"seller": seller, "item_id": item.get("itemId", "")},
                )
            )
        return results


def _shipping_cost(item: dict[str, Any]) -> Decimal | None:
    """Read the first shipping option's cost.

    Returns ``None`` when the item carries no ``shippingOptions`` (shipping unknown);
    a machine-formatted ``"0.0"`` reads as ``Decimal("0")`` (free shipping).
    """
    options = item.get("shippingOptions")
    if not options:
        return None
    cost = (options[0].get("shippingCost") or {}).get("value")
    return parse_price_loose(cost)


def _coerce_ttl(value: object) -> float:
    """Coerce an ``expires_in`` value to seconds, defaulting when it is unusable."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return _DEFAULT_TOKEN_TTL_SECONDS
