"""Unit tests for conservative snippet price extraction."""

from decimal import Decimal

import pytest

from repricing_engine.models.enums import Market
from repricing_engine.sources.snippet_price import extract_price_from_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("In offerta a € 129,90 spedizione gratis", Decimal("129.90")),
        ("Prezzo 1.299,00 € IVA inclusa", Decimal("1299.00")),
        ("Solo EUR 79,99", Decimal("79.99")),
        ("€ 45", Decimal("45")),
    ],
)
def test_extracts_currency_adjacent_price(text, expected):
    assert extract_price_from_text(text, Market.IT) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Fantini Cosmi ECOCOMFORT PLUS 160mm",  # dimensions, not a price
        "Codice 110.791.00.1",  # a product code
        "Spedizione in 24 ore",
        "",
    ],
)
def test_ignores_non_currency_numbers(text):
    assert extract_price_from_text(text, Market.IT) is None


def test_none_text_returns_none():
    assert extract_price_from_text(None, Market.IT) is None


def test_takes_first_currency_amount():
    # A "was/now" snippet: the first EUR-adjacent amount is returned.
    assert extract_price_from_text("da € 99,00 a € 79,00", Market.IT) == Decimal("99.00")


def test_rejects_implausibly_large_amount():
    # A mis-read like "€ 99.000" is almost never a real single-item price -> ignored.
    assert extract_price_from_text("Spedizione su oltre € 99.000 prodotti", Market.IT) is None


def test_accepts_amount_at_plausible_ceiling():
    assert extract_price_from_text("€ 50000", Market.IT) == Decimal("50000")
