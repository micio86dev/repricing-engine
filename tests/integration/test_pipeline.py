"""End-to-end pipeline test over the sample fixtures (offline, AI skipped)."""

import csv
from pathlib import Path

import polars as pl

from repricing_engine.ingestion.catalog_ingestor import CatalogIngestor
from repricing_engine.ingestion.oxylabs_ingestor import OxyLabsIngestor
from repricing_engine.matching.pipeline import MatchingPipeline
from repricing_engine.matching.scoring import confidence_to_level
from repricing_engine.output.csv_writer import RESULT_COLUMNS, CsvWriter


def _load_expected(path: Path) -> dict[str, tuple[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return {
            row["catalog_sku"]: (row["match_method"], row["confidence_level"])
            for row in csv.DictReader(handle)
        }


class TestPipelineEndToEnd:
    def test_full_run_matches_expected(
        self,
        sample_catalog_path: Path,
        sample_oxylabs_path: Path,
        expected_output_path: Path,
        settings,
        fake_encoder,
        tmp_path: Path,
    ):
        catalog = CatalogIngestor().ingest(sample_catalog_path)
        competitors = OxyLabsIngestor().ingest(sample_oxylabs_path)
        expected = _load_expected(expected_output_path)

        pipeline = MatchingPipeline(settings, skip_ai=True, encoder=fake_encoder)
        results = pipeline.run(catalog, competitors, show_progress=False)

        assert len(results) == 24
        assert all(r.is_matched for r in results)

        for result in results:
            sku = result.catalog_product.sku
            method, level = expected[sku]
            best = result.best_match
            assert str(best.match_method) == method
            assert str(confidence_to_level(best.confidence)) == level

    def test_brand_conflict_and_fuzzy_produce_extra_candidates(
        self,
        sample_catalog_path: Path,
        sample_oxylabs_path: Path,
        settings,
        fake_encoder,
    ):
        catalog = CatalogIngestor().ingest(sample_catalog_path)
        competitors = OxyLabsIngestor().ingest(sample_oxylabs_path)
        pipeline = MatchingPipeline(settings, skip_ai=True, encoder=fake_encoder)
        by_sku = {
            r.catalog_product.sku: r
            for r in pipeline.run(catalog, competitors, show_progress=False)
        }

        # iPhone: the correct listing matches on SKU (1.0) AND EAN; a separate
        # same-EAN/wrong-brand competitor is demoted to 0.70 by the brand check.
        iphone = by_sku["APL-IPH13-128"]
        confidences = sorted((c.confidence for c in iphone.all_candidates), reverse=True)
        assert confidences[0] == 1.0
        assert 0.70 in confidences

        # Galaxy: correct EAN match + a fuzzy-SKU refurbished listing.
        galaxy = by_sku["SAM-GAL-S21"]
        assert len(galaxy.all_candidates) >= 2

    def test_writes_results_csv(
        self,
        sample_catalog_path: Path,
        sample_oxylabs_path: Path,
        settings,
        fake_encoder,
        tmp_path: Path,
    ):
        catalog = CatalogIngestor().ingest(sample_catalog_path)
        competitors = OxyLabsIngestor().ingest(sample_oxylabs_path)
        pipeline = MatchingPipeline(settings, skip_ai=True, encoder=fake_encoder)
        results = pipeline.run(catalog, competitors, show_progress=False)

        out = tmp_path / "results.csv"
        stats = CsvWriter().write(results, out)

        assert out.exists()
        frame = pl.read_csv(out)
        assert frame.columns == list(RESULT_COLUMNS)
        assert frame.height == 24
        assert stats["matched_products"] == 24
        assert stats["match_rate_pct"] == 100.0
