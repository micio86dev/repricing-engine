"""Best-effort price extraction from a SERP title/snippet.

A metasearch snippet sometimes carries a visible price (``"... € 129,90 ..."``).
We extract it only when a currency symbol/word is directly adjacent to the number
(to avoid mistaking product codes or dimensions for prices). This is a *bootstrap*
value: when ``--fetch`` reads the real PDP the authoritative price overwrites it,
so a conservative miss here is cheap while a false positive would be costly.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import TYPE_CHECKING

from repricing_engine.exceptions import NormalizationError
from repricing_engine.normalization.price import normalize_price

if TYPE_CHECKING:
    from repricing_engine.models.enums import Market

# Snippet parsing is best-effort; reject implausibly large amounts that are almost
# always a mis-read (a code, a review count, a "20.000 prodotti" phrase near a €).
# Real PDP verification supplies the authoritative price for genuine high-value items.
_MAX_PLAUSIBLE_SNIPPET_PRICE = Decimal("50000")

# A monetary amount: either thousands-grouped (needs a real separator group, so a
# plain "50000" is not clipped to "500" by the leading \d{1,3}) or a plain
# integer/decimal. Optional 2-decimal fraction.
_AMOUNT = r"\d{1,3}(?:[.  ]\d{3})+(?:,\d{2})?|\d+(?:,\d{2})?"
# The amount must sit next to a EUR marker, on either side.
_PRICE_RE = re.compile(
    rf"(?:€|eur)\s*({_AMOUNT})|({_AMOUNT})\s*(?:€|eur)",
    re.IGNORECASE,
)


def extract_price_from_text(text: str | None, market: Market) -> Decimal | None:
    """Return the first EUR-adjacent price found in ``text``, or ``None``.

    Args:
        text: The snippet/title text to scan.
        market: Market whose number formatting rules to apply when parsing.

    Returns:
        The parsed price as a :class:`~decimal.Decimal`, or ``None`` when no
        currency-adjacent amount is present or it can't be parsed.
    """
    if not text:
        return None
    for match in _PRICE_RE.finditer(text):
        raw = match.group(1) or match.group(2)
        if not raw:
            continue
        try:
            price = normalize_price(raw, "EUR", market)
        except NormalizationError:
            continue
        if Decimal(0) < price <= _MAX_PLAUSIBLE_SNIPPET_PRICE:
            return price
    return None
