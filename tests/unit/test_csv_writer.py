"""Unit tests for the CSV result writer."""

from decimal import Decimal
from pathlib import Path

import polars as pl

from repricing_engine.models.enums import Market, MatchMethod
from repricing_engine.models.match_result import MatchResult
from repricing_engine.models.product import (
    CatalogProduct,
    CompetitorProduct,
    MatchCandidate,
)
from repricing_engine.output.csv_writer import RESULT_COLUMNS, CsvWriter


def _matched_result() -> MatchResult:
    catalog = CatalogProduct(
        sku="APL-IPH13-128",
        ean="4006381333931",
        brand="Apple",
        title="Apple iPhone 13",
        category="Smartphones",
        market=Market.IT,
    )
    competitor = CompetitorProduct(
        source="oxylabs",
        source_id="OXY-1",
        title="Apple iPhone 13 Blue",
        price=Decimal("789.00"),
        currency="EUR",
        url="https://example.com/p1",
        market=Market.IT,
        brand="Apple",
        shipping_cost=Decimal("4.99"),
    )
    candidate = MatchCandidate(
        competitor_product=competitor,
        confidence=0.95,
        match_method=MatchMethod.EXACT_EAN,
        layer_source="exact_id",
        match_details={"matched_on": "ean"},
    )
    return MatchResult(
        catalog_product=catalog,
        best_match=candidate,
        all_candidates=[candidate],
        rejected_candidates=[],
        processing_time_ms=1.2,
    )


def _unmatched_result() -> MatchResult:
    catalog = CatalogProduct(
        sku="NO-MATCH",
        brand="Acme",
        title="Mystery Widget",
        category="Misc",
        market=Market.DE,
    )
    return MatchResult(catalog_product=catalog, best_match=None)


class TestCsvWriter:
    def test_writes_results_csv(self, tmp_path: Path):
        out = tmp_path / "results.csv"
        CsvWriter().write([_matched_result(), _unmatched_result()], out)

        assert out.exists()
        frame = pl.read_csv(out)
        assert frame.columns == list(RESULT_COLUMNS)
        assert frame.height == 2

        matched_row = frame.row(0, named=True)
        assert matched_row["catalog_sku"] == "APL-IPH13-128"
        assert matched_row["matched_price"] == "789.00"
        assert matched_row["confidence_level"] == "HIGH"
        assert matched_row["match_method"] == "EXACT_EAN"

        unmatched_row = frame.row(1, named=True)
        assert unmatched_row["matched_source"] in (None, "")

    def test_stats_sidecar_is_written(self, tmp_path: Path):
        out = tmp_path / "results.csv"
        CsvWriter().write([_matched_result(), _unmatched_result()], out)
        stats_file = tmp_path / "results_stats.csv"
        assert stats_file.exists()

    def test_compute_stats(self):
        stats = CsvWriter().compute_stats([_matched_result(), _unmatched_result()])
        assert stats["total_products"] == 2
        assert stats["matched_products"] == 1
        assert stats["match_rate_pct"] == 50.0
        assert stats["match_method_distribution"] == {"EXACT_EAN": 1}
        assert stats["confidence_distribution"] == {"HIGH": 1}
