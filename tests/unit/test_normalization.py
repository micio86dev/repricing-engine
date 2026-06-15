"""Unit tests for the normalization helpers."""

from decimal import Decimal

import pytest

from repricing_engine.exceptions import NormalizationError
from repricing_engine.models.enums import Market
from repricing_engine.normalization.identifiers import (
    normalize_ean,
    normalize_gtin,
    normalize_sku,
)
from repricing_engine.normalization.price import extract_shipping, normalize_price
from repricing_engine.normalization.text import (
    extract_brand,
    normalize_brand,
    normalize_title,
)


class TestNormalizeTitle:
    def test_lowercases_and_strips_punctuation(self):
        assert normalize_title("Apple iPhone 13 Pro!!") == "apple iphone 13 pro"

    def test_collapses_whitespace_and_hyphens(self):
        assert normalize_title("  Sony   WH-1000XM4  ") == "sony wh 1000xm4"

    def test_strips_accents(self):
        assert normalize_title("Café Noir Edição") == "cafe noir edicao"

    def test_expands_abbreviations(self):
        assert normalize_title("Cable w/ Adapter") == "cable with adapter"
        assert normalize_title("Black & Decker") == "black and decker"

    def test_empty_input(self):
        assert normalize_title("") == ""


class TestNormalizeBrand:
    def test_maps_known_alias(self):
        assert normalize_brand("Hewlett-Packard") == "hp"
        assert normalize_brand("Apple Inc") == "apple"

    def test_lowercases_and_trims(self):
        assert normalize_brand("  Sony  ") == "sony"
        assert normalize_brand("HP") == "hp"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_empty_returns_none(self, value):
        assert normalize_brand(value) is None


class TestExtractBrand:
    def test_finds_known_brand(self):
        assert extract_brand("Samsung Galaxy S21 Ultra") == "samsung"
        assert extract_brand("Genuine HP Ink Cartridge 304") == "hp"

    def test_no_known_brand(self):
        assert extract_brand("Generic USB Cable 2m") is None

    def test_empty(self):
        assert extract_brand("") is None


class TestNormalizeEan:
    @pytest.mark.parametrize(
        "value",
        ["4006381333931", "5901234123457", "96385074"],
    )
    def test_valid_codes(self, value):
        assert normalize_ean(value) == value

    def test_strips_surrounding_noise(self):
        assert normalize_ean(" 4006381333931 ") == "4006381333931"

    def test_upc12_padded_to_ean13(self):
        assert normalize_ean("036000291452") == "0036000291452"

    def test_invalid_checksum(self):
        assert normalize_ean("4006381333930") is None

    @pytest.mark.parametrize("value", ["12345", "", None, "abcdefgh"])
    def test_invalid_inputs(self, value):
        assert normalize_ean(value) is None


class TestNormalizeGtin:
    @pytest.mark.parametrize("value", ["4006381333931", "96385074", "036000291452"])
    def test_valid(self, value):
        assert normalize_gtin(value) == value

    @pytest.mark.parametrize("value", ["1234567890", "", None])
    def test_invalid_length(self, value):
        assert normalize_gtin(value) is None


class TestNormalizeSku:
    def test_uppercases_and_strips_whitespace(self):
        assert normalize_sku(" ab-12 cd ") == "AB-12CD"

    def test_int_input(self):
        assert normalize_sku(12345) == "12345"

    def test_none(self):
        assert normalize_sku(None) == ""


class TestNormalizePrice:
    def test_european_format(self):
        assert normalize_price("1.299,90", "EUR", Market.IT) == Decimal("1299.90")
        assert normalize_price("19,99", "EUR", Market.DE) == Decimal("19.99")

    def test_anglo_format(self):
        assert normalize_price("1,299.90", "GBP", Market.UK) == Decimal("1299.90")
        assert normalize_price("19.99", "USD", Market.US) == Decimal("19.99")

    def test_strips_currency_symbol(self):
        assert normalize_price("€ 1.234,56", "EUR", Market.DE) == Decimal("1234.56")

    def test_passthrough_numeric(self):
        assert normalize_price(Decimal("5.00"), "EUR", Market.IT) == Decimal("5.00")
        assert normalize_price(10, "EUR", Market.IT) == Decimal("10")

    @pytest.mark.parametrize("value", ["", "   ", "abc"])
    def test_invalid_raises(self, value):
        with pytest.raises(NormalizationError):
            normalize_price(value, "EUR", Market.IT)


class TestExtractShipping:
    def test_oxylabs_shipping_key(self):
        assert extract_shipping({"shipping": "4,99"}, "oxylabs") == Decimal("4.99")

    def test_delivery_cost_key_case_insensitive(self):
        assert extract_shipping({"Delivery_Cost": "€ 7.50"}, "oxylabs") == Decimal("7.50")

    def test_thousands_only_dot(self):
        assert extract_shipping({"shipping": "1.234"}, "oxylabs") == Decimal("1234")

    @pytest.mark.parametrize(
        "raw",
        [{}, {"price": "10"}, {"shipping": ""}],
    )
    def test_absent_returns_none(self, raw):
        assert extract_shipping(raw, "oxylabs") is None
