"""Unit tests for the CSV ingestors."""

from decimal import Decimal
from pathlib import Path

import pytest

from repricing_engine.exceptions import IngestionError
from repricing_engine.ingestion.catalog_ingestor import CatalogIngestor
from repricing_engine.ingestion.oxylabs_ingestor import OxyLabsIngestor
from repricing_engine.models.enums import Market


class TestCatalogIngestor:
    def test_ingests_sample_catalog(self, sample_catalog_path):
        products = CatalogIngestor().ingest(sample_catalog_path)
        assert len(products) == 24

        first = products[0]
        assert first.sku == "APL-IPH13-128"
        assert first.ean == "4006381333931"
        assert first.brand == "Apple"
        assert first.market == Market.IT

    def test_missing_required_field_raises(self, tmp_path: Path):
        csv = tmp_path / "bad.csv"
        csv.write_text("sku,brand,title\nX1,,Widget\n", encoding="utf-8")
        with pytest.raises(IngestionError):
            CatalogIngestor().ingest(csv)

    def test_handles_bom(self, tmp_path: Path):
        csv = tmp_path / "bom.csv"
        csv.write_text(
            "sku,ean,brand,title,category\nX1,,Acme,Widget,Misc\n",
            encoding="utf-8-sig",
        )
        products = CatalogIngestor(default_market=Market.DE).ingest(csv)
        assert products[0].sku == "X1"
        assert products[0].ean is None
        assert products[0].market == Market.DE

    def test_excel_scientific_notation_ean_is_rejected(self, tmp_path: Path):
        # Excel mangles "8025863058632" into "8,02586E+12"; stripping non-digits
        # would coincidentally yield a valid EAN-8. It must become None (recoverable
        # later from product pages), never a wrong barcode.
        csv = tmp_path / "sci.csv"
        csv.write_text(
            'sku,ean,brand,title,market\nAP19993,"8,02586E+12",Fantini Cosmi,ECOCOMFORT,IT\n',
            encoding="utf-8",
        )
        product = CatalogIngestor().ingest(csv)[0]
        assert product.ean is None
        assert product.gtin is None

    def test_valid_ean_still_parsed(self, tmp_path: Path):
        csv = tmp_path / "ok.csv"
        csv.write_text(
            "sku,ean,brand,title,market\nX1,4006381333931,Acme,Widget,IT\n",
            encoding="utf-8",
        )
        assert CatalogIngestor().ingest(csv)[0].ean == "4006381333931"

    def test_unmapped_columns_go_to_attributes(self, tmp_path: Path):
        csv = tmp_path / "attrs.csv"
        csv.write_text(
            "sku,brand,title,color,size\nX1,Acme,Widget,Red,Large\n",
            encoding="utf-8",
        )
        product = CatalogIngestor().ingest(csv)[0]
        assert product.attributes == {"color": "Red", "size": "Large"}


class TestOxyLabsIngestor:
    def test_ingests_sample_export(self, sample_oxylabs_path):
        products = OxyLabsIngestor().ingest(sample_oxylabs_path)
        assert len(products) == 27
        by_id = {p.source_id: p for p in products}

        first = by_id["OXY-001"]
        assert first.source == "oxylabs"
        assert first.price == Decimal("789.00")
        assert first.currency == "EUR"

    def test_european_price_and_shipping_parsed(self, sample_oxylabs_path):
        by_id = {p.source_id: p for p in OxyLabsIngestor().ingest(sample_oxylabs_path)}
        product = by_id["OXY-002"]
        assert product.price == Decimal("599.90")
        assert product.shipping_cost == Decimal("4.99")

    def test_empty_optional_fields_become_none(self, sample_oxylabs_path):
        by_id = {p.source_id: p for p in OxyLabsIngestor().ingest(sample_oxylabs_path)}
        assert by_id["OXY-003"].sku is None  # blank sku column
        assert by_id["OXY-102"].ean is None  # blank ean column

    def test_thousands_price_parsed(self, sample_oxylabs_path):
        by_id = {p.source_id: p for p in OxyLabsIngestor().ingest(sample_oxylabs_path)}
        assert by_id["OXY-005"].price == Decimal("1299.00")

    def test_raw_data_preserved(self, sample_oxylabs_path):
        product = OxyLabsIngestor().ingest(sample_oxylabs_path)[0]
        assert "product_id" in product.raw_data

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(IngestionError):
            OxyLabsIngestor().ingest(tmp_path / "nope.csv")
