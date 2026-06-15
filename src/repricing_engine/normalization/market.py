"""Detect the target market from the data, so callers need not always pass it.

Signals, in priority order:
1. An explicit market / country column in the catalog (the strongest signal).
2. Competitor URL top-level domains (``.it`` -> IT, ``.de`` -> DE, ...).
3. Currency codes that map 1:1 to a market (GBP -> UK, USD -> US).

Falls back to :data:`DEFAULT_MARKET` when nothing is conclusive. The detected
market drives locale-specific parsing (number format, currency) downstream, so
the language never has to be passed explicitly — it is inferred.
"""

from __future__ import annotations

from collections import Counter
from urllib.parse import urlparse

from repricing_engine.models.enums import Market

DEFAULT_MARKET = Market.IT

_CATALOG_MARKET_KEYS: tuple[str, ...] = ("market", "country", "mercato")
_COMPETITOR_URL_KEYS: tuple[str, ...] = ("url", "product_url", "link")
_CURRENCY_KEYS: tuple[str, ...] = ("currency", "price_currency", "valuta")

_TLD_TO_MARKET: dict[str, Market] = {
    "it": Market.IT,
    "de": Market.DE,
    "fr": Market.FR,
    "es": Market.ES,
    "nl": Market.NL,
    "pt": Market.PT,
    "be": Market.BE,
    "at": Market.AT,
    "uk": Market.UK,
}
_CURRENCY_TO_MARKET: dict[str, Market] = {"GBP": Market.UK, "USD": Market.US}


def _lower(row: dict[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in row.items()}


def _first(row: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _market_from_code(value: str | None) -> Market | None:
    if not value:
        return None
    try:
        return Market(value.strip().upper())
    except ValueError:
        return None


def _market_from_url(url: str) -> Market | None:
    host = urlparse(url if "//" in url else f"//{url}").netloc.lower()
    if not host:
        return None
    if host.endswith(".uk"):  # covers .uk and .co.uk
        return Market.UK
    return _TLD_TO_MARKET.get(host.rsplit(".", 1)[-1])


def detect_market(
    catalog_rows: list[dict[str, str]],
    competitor_rows: list[dict[str, str]] | None = None,
) -> Market:
    """Infer the market from catalog/competitor rows.

    Args:
        catalog_rows: Raw catalog rows (string dicts), as read from the CSV.
        competitor_rows: Optional raw competitor rows for fallback signals.

    Returns:
        The detected :class:`Market`, or :data:`DEFAULT_MARKET` if inconclusive.
    """
    catalog_votes: Counter[Market] = Counter()
    for row in catalog_rows:
        market = _market_from_code(_first(_lower(row), _CATALOG_MARKET_KEYS))
        if market:
            catalog_votes[market] += 1
    if catalog_votes:
        return catalog_votes.most_common(1)[0][0]

    if not competitor_rows:
        return DEFAULT_MARKET

    tld_votes: Counter[Market] = Counter()
    currency_votes: Counter[Market] = Counter()
    for row in competitor_rows:
        lowered = _lower(row)
        url = _first(lowered, _COMPETITOR_URL_KEYS)
        if url:
            market = _market_from_url(url)
            if market:
                tld_votes[market] += 1
        currency = _first(lowered, _CURRENCY_KEYS)
        if currency:
            market = _CURRENCY_TO_MARKET.get(currency.strip().upper())
            if market:
                currency_votes[market] += 1

    if tld_votes:
        return tld_votes.most_common(1)[0][0]
    if currency_votes:
        return currency_votes.most_common(1)[0][0]
    return DEFAULT_MARKET
