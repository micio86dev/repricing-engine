"""Stock-quantity detection in unstructured page text.

Mirrors the precision style of ``price.extract_shipping_from_text``: only an
integer *tightly adjacent* to an Italian/English stock phrase is accepted
(separators only in the gap, never an intervening word or a decimal fragment). A
delivery-time ("in 2 giorni"), a price ("€249,00"), or a bare number never
qualifies, and conflicting counts collapse to ``None``. A wrong quantity is worse
than an unknown one.
"""

from __future__ import annotations

import re

# Stock is a small count; a value at/above this is treated as noise, not stock.
_STOCK_MAX = 100_000

# Number-then-phrase: "3 pezzi disponibili", "solo 2 rimasti", "5 in stock",
# "4 available". An optional unit noun (pezzi/pz/unità) may sit between. The
# lookbehind/lookahead keep the integer off a price decimal ("249,00").
_STOCK_NUM_FIRST_RE = re.compile(
    r"(?<![\d.,])(\d{1,5})(?![.,]?\d)\s*(?:pezzi|pz|unit[àa])?\s*"
    r"\b(?:disponibili|rimasti|rimaste|in\s+stock|available)\b",
    re.IGNORECASE,
)
# Phrase-then-number: "disponibili: 3", "quantità: 10". Only separators may fill
# the gap, so "disponibili in 2 giorni" (a word between) never matches.
_STOCK_KEY_FIRST_RE = re.compile(
    r"\b(?:disponibili|quantit[àa])\b[\s:–—-]{0,3}(?<![\d.,])(\d{1,5})(?![.,]?\d)",
    re.IGNORECASE,
)


def extract_stock_quantity_from_text(text: str) -> int | None:
    """Extract a stock quantity stated next to a stock phrase (free pass, no tokens).

    Precision is favoured over recall: only an integer tightly adjacent to a
    recognised stock phrase counts, and if the page states two *different* counts
    the result is ambiguous and yields ``None``.

    Args:
        text: Visible page text (or HTML) to scan.

    Returns:
        The stock quantity as a non-negative ``int`` (a single, unambiguous
        count in ``[0, 100000)``), or ``None`` when unknown or ambiguous.
    """
    if not text:
        return None
    values: set[int] = set()
    for pattern in (_STOCK_NUM_FIRST_RE, _STOCK_KEY_FIRST_RE):
        for match in pattern.finditer(text):
            value = int(match.group(1))
            if 0 <= value < _STOCK_MAX:
                values.add(value)
    return next(iter(values)) if len(values) == 1 else None
