"""Availability / stock-status normalization across locales (IT / EN).

Competitor feeds report stock in many free-text forms ("Disponibile",
"Esaurito", "In stock", "Su ordinazione", ...). These are mapped onto a small,
stable :class:`~repricing_engine.models.enums.Availability` enum so downstream
consumers (output, repricing rules) can rely on a closed set of values.
"""

from __future__ import annotations

from repricing_engine.models.enums import Availability

# Order matters: out-of-stock and preorder phrases are checked *before* the
# generic in-stock stem, because e.g. "non disponibile" contains "disponibil".
_OUT_OF_STOCK_MARKERS: tuple[str, ...] = (
    "esaurito",
    "non disponibile",
    "no disponibile",
    "indisponibile",
    "out_of_stock",
    "out of stock",
    "outofstock",
    "sold out",
    "terminato",
    "non piu disponibile",
    "non più disponibile",
    "not available",
    "unavailable",
)
_PREORDER_MARKERS: tuple[str, ...] = (
    "preordine",
    "pre-ordine",
    "preorder",
    "pre-order",
    "su ordinazione",
    "su prenotazione",
    "in arrivo",
    "disponibile dal",
    "backorder",
    "back order",
    "prossimamente",
)
# Generic stem catches disponibile / disponibili / disponibilità.
_IN_STOCK_MARKERS: tuple[str, ...] = (
    "disponibil",
    "in_stock",
    "in stock",
    "instock",
    "in magazzino",
    "available",
    "pronta consegna",
    "pronto",
    "consegna in",
    "spedizione in",
    "spedito",
)
_TRUE_TOKENS: frozenset[str] = frozenset({"true", "yes", "si", "sì", "y", "1"})
_FALSE_TOKENS: frozenset[str] = frozenset({"false", "no", "n", "0"})


def normalize_availability(value: str | int | bool | None) -> Availability:
    """Map a raw stock value to an :class:`Availability`.

    Args:
        value: Raw availability text (any locale), a bool, an int (0/1), or
            ``None``.

    Returns:
        The normalized availability; :attr:`Availability.UNKNOWN` when the value
        is empty or unrecognized.
    """
    if value is None:
        return Availability.UNKNOWN
    if isinstance(value, bool):
        return Availability.IN_STOCK if value else Availability.OUT_OF_STOCK

    text = str(value).strip().lower()
    if not text:
        return Availability.UNKNOWN
    if text in _TRUE_TOKENS:
        return Availability.IN_STOCK
    if text in _FALSE_TOKENS:
        return Availability.OUT_OF_STOCK

    for marker in _OUT_OF_STOCK_MARKERS:
        if marker in text:
            return Availability.OUT_OF_STOCK
    for marker in _PREORDER_MARKERS:
        if marker in text:
            return Availability.PREORDER
    for marker in _IN_STOCK_MARKERS:
        if marker in text:
            return Availability.IN_STOCK
    return Availability.UNKNOWN
