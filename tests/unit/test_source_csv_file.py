"""Unit tests for the CSV-file source provider (wraps OxyLabsIngestor)."""

from pathlib import Path

from repricing_engine.models.enums import Market
from repricing_engine.models.product import CatalogProduct
from repricing_engine.sources.providers.csv_file import CsvFileProvider


def _catalog() -> CatalogProduct:
    return CatalogProduct(
        sku="SKU1", brand="Acme", title="Widget", category="Misc", market=Market.IT
    )


class TestCsvFileProvider:
    def test_unavailable_for_missing_file(self, tmp_path: Path):
        provider = CsvFileProvider(tmp_path / "nope.csv", default_market=Market.IT)
        assert provider.is_available() is False

    def test_available_for_existing_file(self, sample_oxylabs_path: Path):
        provider = CsvFileProvider(sample_oxylabs_path, default_market=Market.IT)
        assert provider.is_available() is True

    async def test_search_returns_results_and_caches(self, sample_oxylabs_path: Path):
        provider = CsvFileProvider(sample_oxylabs_path, default_market=Market.IT)
        results = await provider.search(_catalog(), Market.IT)
        assert results, "expected the CSV export to yield results"
        assert all(r.source_provider == "csv_file" for r in results)
        assert any(r.price is not None for r in results)
        # Second call is served from cache (same content).
        again = await provider.search(_catalog(), Market.IT)
        assert len(again) == len(results)
