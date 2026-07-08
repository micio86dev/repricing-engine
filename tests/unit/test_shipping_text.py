"""Unit tests for free/paid shipping extraction from unstructured page text.

Precision matters more than recall here: a wrong shipping number poisons the
landed price, so every ambiguous or threshold-conditioned case must return
``None`` (unknown), never a guessed amount.
"""

from decimal import Decimal

import pytest

from repricing_engine.models.enums import Market
from repricing_engine.normalization.price import extract_shipping_from_text


class TestFreeShipping:
    @pytest.mark.parametrize(
        ("text", "market"),
        [
            ("Spedizione gratuita in 24h", Market.IT),
            ("Consegna gratis su tutti gli ordini", Market.IT),
            ("Spedizione inclusa nel prezzo", Market.IT),
            ("Spedizione compresa", Market.IT),
            ("Free shipping on all orders", Market.IT),
            ("Jetzt mit kostenlosem Versand", Market.DE),
            ("Gratis Versand ab Lager", Market.DE),
            ("versandkostenfrei", Market.DE),
            ("Livraison gratuite sous 48h", Market.FR),
            ("Frais de port offerts", Market.FR),
            ("Envío gratis a toda España", Market.ES),
            ("Envío gratuito", Market.ES),
            ("Gratis verzending vandaag", Market.NL),
            ("Frete grátis para todo o país", Market.PT),
        ],
    )
    def test_unconditional_free_shipping_is_zero(self, text: str, market: Market):
        assert extract_shipping_from_text(text, market) == Decimal("0")


class TestPaidShipping:
    @pytest.mark.parametrize(
        ("text", "market", "expected"),
        [
            ("Spedizione: 4,99 €", Market.IT, Decimal("4.99")),
            ("Costi di spedizione € 5,90", Market.IT, Decimal("5.90")),
            ("Spedizione 2,99€ per l'Italia", Market.IT, Decimal("2.99")),
            ("Versand: 4,99 €", Market.DE, Decimal("4.99")),
            ("Livraison 3,50 €", Market.FR, Decimal("3.50")),
            ("Envío 2,95 €", Market.ES, Decimal("2.95")),
            ("Shipping: $5.99", Market.US, Decimal("5.99")),
        ],
    )
    def test_reads_amount_adjacent_to_keyword(self, text: str, market: Market, expected: Decimal):
        assert extract_shipping_from_text(text, market) == expected

    def test_picks_the_shipping_amount_not_the_product_price(self):
        # The product price sits after the shipping cost; the tight forward window
        # must lock onto 4,99 (adjacent to "Spedizione"), never 199,00.
        text = "Spedizione: 4,99€. Prezzo di listino €199,00"
        assert extract_shipping_from_text(text, Market.IT) == Decimal("4.99")


class TestConditionalOrAmbiguousReturnsNone:
    @pytest.mark.parametrize(
        ("text", "market"),
        [
            ("Spedizione gratuita sopra 99€", Market.IT),
            ("Spedizione gratuita per ordini superiori a 50€", Market.IT),
            ("Free shipping over €50", Market.IT),
            ("Livraison gratuite dès 25€", Market.FR),
            ("Envío gratis a partir de 30€", Market.ES),
            ("Kostenloser Versand ab 25€", Market.DE),
            # Threshold expressed with $ / £ (not only €).
            ("Free shipping over $50", Market.US),
            ("Free delivery from £30", Market.UK),
            ("Kostenloser Versand ab $20", Market.DE),
            # Italian "da €X" conditional (regression guard).
            ("Spedizione gratuita da €25", Market.IT),
            ("Spedizione gratis da €50", Market.IT),
            ("Spedizione gratuita da 30€", Market.IT),
        ],
    )
    def test_threshold_free_shipping_is_unknown(self, text: str, market: Market):
        assert extract_shipping_from_text(text, market) is None


class TestFalsePositivesAreRejected:
    @pytest.mark.parametrize(
        ("text", "market"),
        [
            # A shipping word with no currency-tagged amount nearby.
            ("Spedizione veloce garantita in 2 giorni", Market.IT),
            # A product price present, but far from / not tied to a shipping word.
            ("Prodotto top di gamma. Prezzo €249,00", Market.IT),
            # Store pickup, no shipping cost stated.
            ("Ritiro gratuito in negozio", Market.IT),
            ("", Market.IT),
            ("Nessuna informazione disponibile", Market.IT),
            # A 4-digit product price must not be truncated into a small "cost".
            ("Shipping €1234,00", Market.IT),
            ("Shipping €1999,00", Market.IT),
            # Keyword inside an HTML attribute, product price in the cell body.
            ('<td data-type="shipping">€239.00</td>', Market.IT),
            # Delivery-time sentence carrying the product price further along.
            ("Shipping in 2 days €299.00", Market.IT),
            # "Porto" as a place name, not "spese di porto".
            ("Porto Rico €599 Vacanze", Market.IT),
            # Shipping word with a gap of real words before the amount.
            ("Spedizione veloce, prezzo top €249,00", Market.IT),
        ],
    )
    def test_returns_none(self, text: str, market: Market):
        assert extract_shipping_from_text(text, market) is None


class TestMultipleShippingTermsPicksCheapest:
    def test_returns_the_lowest_stated_paid_amount(self):
        # Two shipping tiers on one page; the cheapest is the achievable floor.
        text = "Spedizione: 9,99€. Consegna: 4,99€"
        assert extract_shipping_from_text(text, Market.IT) == Decimal("4.99")
