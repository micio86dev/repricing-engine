"""Unit tests for the per-offer price-landscape writer."""

from decimal import Decimal
from pathlib import Path

import polars as pl

from repricing_engine.models.enums import Availability, Market, MatchMethod
from repricing_engine.models.match_result import MatchResult
from repricing_engine.models.product import (
    CatalogProduct,
    CompetitorProduct,
    MatchCandidate,
)
from repricing_engine.output.landscape_writer import (
    RAW_COLUMNS,
    SUMMARY_COLUMNS,
    LandscapeCsvWriter,
)


def _candidate(seller: str, price: str, shipping: str | None, availability: Availability):
    competitor = CompetitorProduct(
        source="organica",
        source_id=seller,
        title=f"Comp {seller}",
        price=Decimal(price),
        currency="EUR",
        url=f"https://www.{seller}.it/p",
        market=Market.IT,
        brand="Acme",
        shipping_cost=None if shipping is None else Decimal(shipping),
        availability=availability,
        seller=seller,
        scraped_at="2026-06-15",
    )
    return MatchCandidate(
        competitor_product=competitor,
        confidence=0.97,
        match_method=MatchMethod.AI_VERIFIED,
        layer_source="sku_brand",
        match_details={"fuzzy_score": 100},
    )


def _matched(our_price: Decimal | None = None, cogs: Decimal | None = None) -> MatchResult:
    catalog = CatalogProduct(
        sku="SKU1",
        brand="Acme",
        title="Widget",
        category="Misc",
        market=Market.IT,
        price=our_price,
        cogs=cogs,
        currency="EUR",
    )
    alpha = _candidate("alpha", "100.00", "0.00", Availability.IN_STOCK)  # landed 100
    beta = _candidate("beta", "90.00", "5.00", Availability.OUT_OF_STOCK)  # landed 95 -> cheapest
    return MatchResult(catalog_product=catalog, best_match=alpha, all_candidates=[alpha, beta])


def _unmatched() -> MatchResult:
    catalog = CatalogProduct(
        sku="SKU2", brand="Acme", title="Mystery", category="Misc", market=Market.DE
    )
    return MatchResult(catalog_product=catalog, best_match=None)


def _read(path: Path) -> pl.DataFrame:
    # Force every column to Utf8 so "95.00" stays a string, not a parsed float.
    return pl.read_csv(path, infer_schema_length=0)


class TestLandscapeWriter:
    def test_one_row_per_offer_plus_placeholder(self, tmp_path: Path):
        out = tmp_path / "results.csv"
        LandscapeCsvWriter().write([_matched(), _unmatched()], out)
        frame = _read(out)
        assert frame.columns == list(RAW_COLUMNS)
        assert frame.height == 3  # 2 offers for SKU1 + 1 placeholder for SKU2

    def test_rows_sorted_by_landed_with_fields(self, tmp_path: Path):
        out = tmp_path / "results.csv"
        LandscapeCsvWriter().write([_matched()], out)
        first = _read(out).row(0, named=True)
        assert first["competitor_name"] == "beta"
        assert first["competitor_landed"] == "95.00"
        assert first["competitor_domain"] == "beta.it"
        assert first["in_stock"] == "no"
        assert first["confidence"] == "PDP·SKU"
        assert first["match_field"] == "sku"
        assert first["source"] == "organica"
        assert first["scraped_at"] == "2026-06-15"

    def test_summary_min_median_position(self, tmp_path: Path):
        out = tmp_path / "results.csv"
        LandscapeCsvWriter().write(
            [_matched(our_price=Decimal("110.00"), cogs=Decimal("70.00"))], out
        )
        row = _read(out.with_name("results_summary.csv")).row(0, named=True)
        assert row["n_competitor"] == "2"
        assert row["min_landed"] == "95.00"
        assert row["max_landed"] == "100.00"
        assert row["median_landed"] == "97.50"
        assert row["cheapest_competitor"] == "beta"
        assert row["position"] == "3"  # both competitors cheaper than our 110 -> 3rd
        assert row["suggested_price"] == "95.00"

    def test_suggested_floored_at_cogs(self, tmp_path: Path):
        out = tmp_path / "results.csv"
        LandscapeCsvWriter().write(
            [_matched(our_price=Decimal("110.00"), cogs=Decimal("99.00"))], out
        )
        row = _read(out.with_name("results_summary.csv")).row(0, named=True)
        # Cheapest landed (95) is below COGS (99) -> never suggest below cost.
        assert row["suggested_price"] == "99.00"

    def test_summary_columns_and_stats(self, tmp_path: Path):
        out = tmp_path / "results.csv"
        LandscapeCsvWriter().write([_matched(), _unmatched()], out)
        summary = _read(out.with_name("results_summary.csv"))
        assert summary.columns == list(SUMMARY_COLUMNS)

        stats = LandscapeCsvWriter.compute_stats([_matched(), _unmatched()])
        assert stats["total_products"] == 2
        assert stats["matched_products"] == 1
        assert stats["total_offers"] == 2
        assert stats["avg_offers_per_matched"] == 2.0
