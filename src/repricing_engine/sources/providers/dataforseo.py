"""DataForSEO provider — Google Shopping ("Merchant") / Organic SERP offers.

Discovers competitor offers from DataForSEO's live "advanced" endpoints. The
Merchant/Shopping endpoint returns priced offers (title, price, seller, GTIN)
directly, while the SERP endpoint returns URLs; ``mode`` selects which. Auth is
HTTP Basic (``login``:``password``) — the ``login`` is the account email and the
``password`` is the generated API password from the dashboard, not the UI login.
The provider is inert unless both credentials are set.
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from repricing_engine.exceptions import SourceFetchError
from repricing_engine.models.enums import Market
from repricing_engine.normalization.identifiers import normalize_gtin
from repricing_engine.normalization.price import parse_price_loose
from repricing_engine.sources.base import COST_CHEAP, BaseSourceProvider
from repricing_engine.sources.models import RawSearchResult
from repricing_engine.sources.query import brand_title_query

if TYPE_CHECKING:
    from collections.abc import Iterator
    from decimal import Decimal

    from repricing_engine.models.product import CatalogProduct

logger = logging.getLogger(__name__)

_SHOPPING_URL = "https://api.dataforseo.com/v3/merchant/google/products/live/advanced"
_SERP_URL = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"
# A body ``status_code`` of 20000 is DataForSEO's "OK"; anything else (auth, quota,
# bad request) means there are no usable results in this response.
_SUCCESS_STATUS = 20000

# Market -> (location_code, language_code). Markets without a mapping here are
# skipped rather than sent to a wrong-country locale (which would poison a
# same-market price landscape).
_LOCATIONS: dict[Market, tuple[int, str]] = {
    Market.IT: (2380, "it"),
    Market.DE: (2276, "de"),
    Market.FR: (2250, "fr"),
    Market.ES: (2724, "es"),
    Market.UK: (2826, "en"),
    Market.US: (2840, "en"),
}


class DataForSeoProvider(BaseSourceProvider):
    """Discover priced competitor offers from the DataForSEO API."""

    name = "dataforseo"
    cost_tier = COST_CHEAP

    def __init__(
        self,
        client: httpx.AsyncClient,
        login: str,
        password: str,
        *,
        mode: str = "shopping",
        enabled: bool = True,
        timeout_seconds: float = 15.0,
        result_limit: int = 20,
        locations: dict[Market, tuple[int, str]] | None = None,
    ) -> None:
        """Create the provider.

        Args:
            client: Injected async HTTP client (tests inject a MockTransport).
            login: DataForSEO account email (HTTP Basic username).
            password: DataForSEO API password (HTTP Basic password).
            mode: ``"shopping"`` (Merchant, priced offers) or ``"serp"`` (URLs).
            enabled: Master on/off switch from settings.
            timeout_seconds: Per-request timeout.
            result_limit: Max results kept per search.
            locations: Optional override of the ``Market`` -> (location_code,
                language_code) map.
        """
        self._client = client
        self._login = login or ""
        self._password = password or ""
        self._enabled = enabled
        self._timeout = timeout_seconds
        self._result_limit = max(1, result_limit)
        self._locations = locations or dict(_LOCATIONS)
        self._url = _SERP_URL if mode == "serp" else _SHOPPING_URL

    def is_available(self) -> bool:
        """True only when enabled and both credentials are configured."""
        if not self._enabled:
            return False
        if not (self._login and self._password):
            logger.info(
                "DataForSEO disabled: set DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD "
                "(the API password from https://app.dataforseo.com -> API Access)."
            )
            return False
        return True

    async def search(
        self,
        product: CatalogProduct,
        market: Market,
    ) -> list[RawSearchResult]:
        """Search DataForSEO for ``product`` in ``market``.

        Returns ``[]`` (never raises) when the market is unmapped, no query can be
        built, or any network/parse step fails — one failure must not sink the batch.
        """
        location = self._locations.get(market)
        if location is None:
            logger.info("DataForSEO has no location mapping for %s; skipping.", market)
            return []
        keyword = brand_title_query(product)
        if not keyword:
            return []
        try:
            payload = await self._fetch(keyword, location)
        except (SourceFetchError, httpx.HTTPError) as exc:
            logger.warning("DataForSEO unavailable for %s: %s", product.sku, exc)
            return []
        return self._parse(payload)

    async def _fetch(self, keyword: str, location: tuple[int, str]) -> dict[str, Any]:
        """POST the live "advanced" task and return the parsed JSON body."""
        location_code, language_code = location
        body = [
            {
                "language_code": language_code,
                "location_code": location_code,
                "keyword": keyword,
            }
        ]
        headers = {
            "Authorization": f"Basic {self._basic_auth()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            response = await self._client.post(
                self._url, json=body, headers=headers, timeout=self._timeout
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            msg = f"DataForSEO request failed: {exc}"
            raise SourceFetchError(msg) from exc

    def _basic_auth(self) -> str:
        """Build the HTTP Basic credential from ``login``:``password``."""
        credentials = f"{self._login}:{self._password}".encode()
        return base64.b64encode(credentials).decode("ascii")

    def _parse(self, payload: dict[str, Any]) -> list[RawSearchResult]:
        """Map a DataForSEO payload's nested ``tasks[].result[].items[]`` to results."""
        if payload.get("status_code") != _SUCCESS_STATUS:
            logger.warning(
                "DataForSEO returned status_code %s; treating as no results.",
                payload.get("status_code"),
            )
            return []
        results: list[RawSearchResult] = []
        for item in _iter_items(payload):
            mapped = self._map_item(item)
            if mapped is not None:
                results.append(mapped)
        return results[: self._result_limit]

    def _map_item(self, item: dict[str, Any]) -> RawSearchResult | None:
        """Map one shopping/SERP item to a :class:`RawSearchResult` (``None`` to skip)."""
        title = str(item.get("title") or "").strip()
        url = _first_present(item, ("url", "check_url", "link"))
        price = _extract_price(item)
        if not title or not url or price is None:
            return None
        seller = _first_present(item, ("seller", "shop_name", "source")) or None
        return RawSearchResult(
            source_provider=self.name,
            title=title,
            url=url,
            price=price,
            currency=_extract_currency(item),
            shipping_cost=None,
            domain=urlparse(url).netloc.lower().removeprefix("www."),
            # Re-validate the reported GTIN so a malformed value never poisons a match.
            ean=normalize_gtin(item.get("gtin")),
            raw_data={"seller": seller},
        )


def _iter_items(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield each item dict from the nested ``tasks[].result[].items[]`` structure.

    Every level is guarded (``None``/non-dict entries are skipped) because the API
    frequently returns partially-populated envelopes.
    """
    for task in payload.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        for block in task.get("result") or []:
            if not isinstance(block, dict):
                continue
            for item in block.get("items") or []:
                if isinstance(item, dict):
                    yield item


def _first_present(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    """Return the first non-empty string value among ``keys`` ("" if none)."""
    for key in keys:
        value = item.get(key)
        if value:
            text = str(value).strip()
            if text:
                return text
    return ""


def _extract_price(item: dict[str, Any]) -> Decimal | None:
    """Read a price whose ``price`` field is a number or a nested object.

    A nested object may carry the amount under ``current``/``value``/``price``;
    whatever is present is fed to :func:`parse_price_loose`.
    """
    raw = item.get("price")
    if isinstance(raw, dict):
        for key in ("current", "value", "price"):
            if raw.get(key) is not None:
                return parse_price_loose(raw.get(key))
        return None
    return parse_price_loose(raw)


def _extract_currency(item: dict[str, Any]) -> str | None:
    """Read the currency from the item or its nested price object."""
    raw = item.get("price")
    nested = raw.get("currency") if isinstance(raw, dict) else None
    currency = item.get("currency") or nested
    return (str(currency).strip() or None) if currency else None
