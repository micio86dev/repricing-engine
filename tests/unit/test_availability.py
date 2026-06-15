"""Unit tests for availability normalization and the new ingestion columns."""

from decimal import Decimal
from pathlib import Path

import pytest

from repricing_engine.ingestion.catalog_ingestor import CatalogIngestor
from repricing_engine.ingestion.oxylabs_ingestor import OxyLabsIngestor
from repricing_engine.models.enums import Availability, Market
from repricing_engine.models.product import CompetitorProduct
from repricing_engine.normalization.availability import normalize_availability


class TestNormalizeAvailability:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Disponibile", Availability.IN_STOCK),
            ("In stock", Availability.IN_STOCK),
            ("Disponibilità immediata", Availability.IN_STOCK),
            ("Esaurito", Availability.OUT_OF_STOCK),
            ("Non disponibile", Availability.OUT_OF_STOCK),
            ("Out of stock", Availability.OUT_OF_STOCK),
            ("Su ordinazione", Availability.PREORDER),
            ("Preordine", Availability.PREORDER),
            ("in_stock", Availability.IN_STOCK),
            ("out_of_stock", Availability.OUT_OF_STOCK),
            ("preorder", Availability.PREORDER),
            ("unknown", Availability.UNKNOWN),
            ("", Availability.UNKNOWN),
            ("   ", Availability.UNKNOWN),
            (None, Availability.UNKNOWN),
            ("boh", Availability.UNKNOWN),
            (True, Availability.IN_STOCK),
            (False, Availability.OUT_OF_STOCK),
            (1, Availability.IN_STOCK),
            (0, Availability.OUT_OF_STOCK),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_availability(raw) == expected

    def test_negation_beats_in_stock_stem(self):
        # "non disponibile" contains "disponibil" — must still be OUT_OF_STOCK.
        assert normalize_availability("Prodotto non disponibile") == Availability.OUT_OF_STOCK


class TestCompetitorDefaults:
    def test_availability_defaults_to_unknown(self):
        product = CompetitorProduct(
            source="oxylabs",
            source_id="X",
            title="Widget",
            price=Decimal("10.00"),
            currency="EUR",
            url="https://e.com/x",
            market=Market.IT,
        )
        assert product.availability == Availability.UNKNOWN
        assert product.seller is None
        assert product.scraped_at is None


class TestOxyLabsExtraColumns:
    def test_reads_availability_seller_source_scraped_at(self, tmp_path: Path):
        csv = tmp_path / "oxy.csv"
        csv.write_text(
            "product_id;title;price;currency;availability;seller;source;scraped_at\n"
            "A1;Widget A;10,00;EUR;Disponibile;Shop Uno;organica;2026-06-15\n"
            "A2;Widget B;20,00;EUR;Esaurito;Shop Due;organica;2026-06-15\n"
            "A3;Widget C;30,00;EUR;;;;\n",
            encoding="utf-8",
        )
        by_id = {p.source_id: p for p in OxyLabsIngestor().ingest(csv)}

        assert by_id["A1"].availability == Availability.IN_STOCK
        assert by_id["A1"].seller == "Shop Uno"
        assert by_id["A1"].source == "organica"
        assert by_id["A1"].scraped_at == "2026-06-15"
        assert by_id["A2"].availability == Availability.OUT_OF_STOCK
        # Empty provenance falls back to the default source name.
        assert by_id["A3"].availability == Availability.UNKNOWN
        assert by_id["A3"].source == "oxylabs"
        assert by_id["A3"].seller is None


class TestCatalogPriceCogs:
    def test_reads_price_cogs_currency(self, tmp_path: Path):
        csv = tmp_path / "cat.csv"
        csv.write_text(
            "sku;brand;title;price;cogs;currency\nX1;Acme;Widget;239,00;150,00;EUR\n",
            encoding="utf-8",
        )
        product = CatalogIngestor().ingest(csv)[0]
        assert product.price == Decimal("239.00")
        assert product.cogs == Decimal("150.00")
        assert product.currency == "EUR"

    def test_price_optional(self, tmp_path: Path):
        csv = tmp_path / "cat.csv"
        csv.write_text("sku;brand;title\nX1;Acme;Widget\n", encoding="utf-8")
        product = CatalogIngestor().ingest(csv)[0]
        assert product.price is None
        assert product.cogs is None
