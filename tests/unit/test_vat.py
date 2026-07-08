"""Unit tests for VAT normalization (rates + price split)."""

from decimal import Decimal

from repricing_engine.models.enums import Market
from repricing_engine.normalization.vat import VAT_RATES, split_vat, vat_rate_for


class TestVatRateFor:
    def test_known_markets(self):
        assert vat_rate_for(Market.IT) == Decimal("0.22")
        assert vat_rate_for(Market.DE) == Decimal("0.19")
        assert vat_rate_for(Market.US) == Decimal("0.00")

    def test_rates_table_is_complete_for_every_market(self):
        assert set(VAT_RATES) == set(Market)


class TestSplitVat:
    def test_vat_included_it_22(self):
        # Grounding: elmaxweb.it JSON-LD 211.75, valueAddedTaxIncluded=true.
        ex, vat, incl = split_vat(Decimal("211.75"), included=True, rate=Decimal("0.22"))
        assert ex == Decimal("173.57")
        assert vat == Decimal("38.18")
        assert incl == Decimal("211.75")

    def test_vat_excluded_it_22(self):
        # Grounding: idrocrimart.it JSON-LD 239.00, valueAddedTaxIncluded=false.
        ex, vat, incl = split_vat(Decimal("239.00"), included=False, rate=Decimal("0.22"))
        assert ex == Decimal("239.00")
        assert vat == Decimal("52.58")
        assert incl == Decimal("291.58")

    def test_vat_included_de_19(self):
        ex, vat, incl = split_vat(Decimal("119.00"), included=True, rate=Decimal("0.19"))
        assert ex == Decimal("100.00")
        assert vat == Decimal("19.00")
        assert incl == Decimal("119.00")

    def test_zero_rate_us(self):
        ex, vat, incl = split_vat(Decimal("100.00"), included=True, rate=Decimal("0.00"))
        assert ex == Decimal("100.00")
        assert vat == Decimal("0.00")
        assert incl == Decimal("100.00")
        assert ex == incl

    def test_components_stay_consistent(self):
        ex, vat, incl = split_vat(Decimal("211.75"), included=True, rate=Decimal("0.22"))
        # Independent quantization keeps ex + vat == incl (no off-by-a-cent drift).
        assert ex + vat == incl
