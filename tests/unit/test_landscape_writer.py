"""Unit tests for the per-offer price-landscape writer."""

from decimal import Decimal
from pathlib import Path

import polars as pl

from repricing_engine.models.enums import Availability, Market, MatchMethod, ShippingSource
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


def _candidate(
    seller: str,
    price: str,
    shipping: str | None,
    availability: Availability,
    *,
    stock: int | None = None,
    shipping_source: ShippingSource | None = None,
    vat_included: bool | None = None,
):
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
        shipping_source=shipping_source,
        vat_included=vat_included,
        stock_quantity=stock,
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
        # beta raw 90.00 (VAT-included default) -> ex-VAT 73.77 + 5.00 shipping.
        assert first["competitor_landed"] == "78.77"
        assert first["competitor_domain"] == "beta.it"
        assert first["in_stock"] == "no"
        assert first["confidence"] == "PDP·SKU"
        assert first["match_field"] == "sku"
        assert first["source"] == "organica"
        assert first["scraped_at"] == "2026-06-15"

    def test_emits_stock_qty_and_shipping_source_columns(self, tmp_path: Path):
        catalog = CatalogProduct(
            sku="SKU1", brand="Acme", title="Widget", category="Misc", market=Market.IT
        )
        offer = _candidate(
            "gamma",
            "80.00",
            "0.00",
            Availability.IN_STOCK,
            stock=5,
            shipping_source=ShippingSource.RULE,
        )
        result = MatchResult(catalog_product=catalog, best_match=offer, all_candidates=[offer])
        out = tmp_path / "results.csv"
        LandscapeCsvWriter().write([result], out)
        frame = _read(out)
        assert "competitor_stock_qty" in frame.columns
        assert "shipping_source" in frame.columns
        first = frame.row(0, named=True)
        assert first["competitor_stock_qty"] == "5"
        assert first["shipping_source"] == "rule"

    def test_missing_stock_and_source_are_blank(self, tmp_path: Path):
        out = tmp_path / "results.csv"
        LandscapeCsvWriter().write([_matched()], out)
        first = _read(out).row(0, named=True)
        # An unset field reads back as an empty string or null (polars); never a value.
        assert first["competitor_stock_qty"] in ("", None)
        assert first["shipping_source"] in ("", None)

    def test_summary_min_median_position(self, tmp_path: Path):
        out = tmp_path / "results.csv"
        LandscapeCsvWriter().write(
            [_matched(our_price=Decimal("110.00"), cogs=Decimal("70.00"))], out
        )
        row = _read(out.with_name("results_summary.csv")).row(0, named=True)
        assert row["n_competitor"] == "2"
        # Landed aggregates are ex-VAT: beta 73.77+5=78.77, alpha 81.97+0=81.97.
        assert row["min_landed"] == "78.77"
        assert row["max_landed"] == "81.97"
        assert row["median_landed"] == "80.37"
        assert row["cheapest_competitor"] == "beta"
        assert row["position"] == "3"  # both competitors cheaper than our 110 -> 3rd
        assert row["suggested_price"] == "78.77"

    def test_suggested_floored_at_cogs(self, tmp_path: Path):
        out = tmp_path / "results.csv"
        LandscapeCsvWriter().write(
            [_matched(our_price=Decimal("110.00"), cogs=Decimal("99.00"))], out
        )
        row = _read(out.with_name("results_summary.csv")).row(0, named=True)
        # Cheapest ex-VAT landed (78.77) is below COGS (99) -> never suggest below cost.
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


class TestVatColumns:
    @staticmethod
    def _write_single(offer, tmp_path: Path) -> dict:
        catalog = CatalogProduct(
            sku="SKU1", brand="Acme", title="Widget", category="Misc", market=Market.IT
        )
        result = MatchResult(catalog_product=catalog, best_match=offer, all_candidates=[offer])
        out = tmp_path / "results.csv"
        LandscapeCsvWriter().write([result], out)
        return _read(out).row(0, named=True)

    def test_price_is_ex_vat_by_default_included(self, tmp_path: Path):
        # No vat_included flag -> treated as VAT-included (IT B2C default).
        offer = _candidate("alpha", "211.75", "0.00", Availability.IN_STOCK)
        row = self._write_single(offer, tmp_path)
        assert row["competitor_price"] == "173.57"  # ex-VAT
        assert row["competitor_vat"] == "38.18"
        assert row["competitor_price_incl_vat"] == "211.75"
        assert row["competitor_landed"] == "173.57"  # ex-VAT + 0 shipping

    def test_vat_excluded_price_kept_as_is(self, tmp_path: Path):
        # Grounding: idrocrimart.it declares its price VAT-excluded.
        offer = _candidate("beta", "239.00", "0.00", Availability.IN_STOCK, vat_included=False)
        row = self._write_single(offer, tmp_path)
        assert row["competitor_price"] == "239.00"  # already ex-VAT
        assert row["competitor_vat"] == "52.58"
        assert row["competitor_price_incl_vat"] == "291.58"

    def test_unpriced_offer_leaves_vat_columns_blank(self, tmp_path: Path):
        offer = _candidate("gamma", "0", None, Availability.UNKNOWN)
        priceless = offer.model_copy(
            update={
                "competitor_product": offer.competitor_product.model_copy(update={"price": None})
            }
        )
        row = self._write_single(priceless, tmp_path)
        assert row["competitor_price"] in ("", None)
        assert row["competitor_vat"] in ("", None)
        assert row["competitor_price_incl_vat"] in ("", None)
