"""Unit tests for stock-quantity extraction from unstructured page text.

Precision matters more than recall: a wrong on-hand count misleads a repricing
decision, so every ambiguous, decimal, or non-adjacent case must return ``None``
(unknown), never a guessed integer.
"""

import pytest

from repricing_engine.normalization.stock import extract_stock_quantity_from_text


class TestStockQuantityAccepted:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("3 pezzi disponibili", 3),
            ("Solo 2 rimasti", 2),
            ("Solo 1 disponibili", 1),
            ("Quantità: 10", 10),
            ("Disponibili: 7", 7),
            ("5 in stock", 5),
            ("4 available", 4),
            ("12 pz disponibili", 12),
            ("Ne restano solo 8 rimaste", 8),
            ("0 pezzi disponibili", 0),  # sold out, but an explicit, valid count
        ],
    )
    def test_reads_count_adjacent_to_phrase(self, text: str, expected: int):
        assert extract_stock_quantity_from_text(text) == expected

    def test_agreeing_counts_collapse_to_the_value(self):
        # Two phrasings state the same number -> unambiguous.
        assert extract_stock_quantity_from_text("3 pezzi disponibili. Disponibili: 3") == 3


class TestStockQuantityRejected:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "Spedizione in 2 giorni",  # delivery time, not stock
            "Consegna in 3-5 giorni lavorativi",
            "€249,00",  # a price, never a count
            "Prezzo di listino 1.299,00 €",
            "Disponibile in 2 giorni",  # a word ("in") breaks the tight gap
            "Disponibilità immediata",  # no integer
            "Prodotto disponibile",  # no integer
            "Garanzia 24 mesi",  # unrelated number
            "5 stelle su Trustpilot",  # unrelated number + noun
            "Spedizione: 4,99€",  # a shipping amount, not stock
        ],
    )
    def test_ambiguous_or_unrelated_is_none(self, text: str):
        assert extract_stock_quantity_from_text(text) is None

    def test_conflicting_counts_are_ambiguous(self):
        # Two different counts -> we cannot trust either.
        assert extract_stock_quantity_from_text("5 disponibili ... Quantità: 10") is None

    def test_six_digit_number_exceeds_cap(self):
        assert extract_stock_quantity_from_text("100000 pezzi disponibili") is None
