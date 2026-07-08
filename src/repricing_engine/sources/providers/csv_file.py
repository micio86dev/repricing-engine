"""CSV-file provider — wraps the existing :class:`OxyLabsIngestor` as a source.

Lets a static competitor export participate in the concurrent source-fetching
flow alongside the online providers. The file is read once and cached; results
are product-agnostic (downstream matching does the per-product filtering).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from repricing_engine.exceptions import IngestionError
from repricing_engine.ingestion.oxylabs_ingestor import OxyLabsIngestor
from repricing_engine.sources.base import COST_FREE, BaseSourceProvider
from repricing_engine.sources.models import RawSearchResult

if TYPE_CHECKING:
    from pathlib import Path

    from repricing_engine.models.enums import Market
    from repricing_engine.models.product import CatalogProduct, CompetitorProduct

logger = logging.getLogger(__name__)


class CsvFileProvider(BaseSourceProvider):
    """Expose a static OxyLabs/competitor CSV export as a source provider."""

    name = "csv_file"
    cost_tier = COST_FREE

    def __init__(self, file_path: Path, *, default_market: Market) -> None:
        """Create the provider.

        Args:
            file_path: Path to the competitor export CSV.
            default_market: Market assumed for rows without a market column.
        """
        self._file_path = file_path
        self._ingestor = OxyLabsIngestor(default_market=default_market)
        self._cache: list[RawSearchResult] | None = None

    def is_available(self) -> bool:
        """True when the configured CSV file exists."""
        return self._file_path.exists()

    async def search(
        self,
        product: CatalogProduct,  # noqa: ARG002 (product-agnostic; matching filters later)
        market: Market,  # noqa: ARG002
    ) -> list[RawSearchResult]:
        """Return every offer in the CSV (read once, then cached)."""
        if self._cache is None:
            self._cache = await asyncio.to_thread(self._load)
        return list(self._cache)

    def _load(self) -> list[RawSearchResult]:
        """Read and convert the CSV file into raw results (blocking)."""
        try:
            products = self._ingestor.ingest(self._file_path)
        except IngestionError:
            logger.exception("CsvFileProvider: failed to ingest %s", self._file_path)
            return []
        return [self._to_result(item) for item in products]

    def _to_result(self, product: CompetitorProduct) -> RawSearchResult:
        """Convert an ingested competitor product back into a raw result."""
        return RawSearchResult(
            source_provider=self.name,
            title=product.title,
            url=product.url,
            price=product.price,
            currency=product.currency,
            shipping_cost=product.shipping_cost,
            domain=urlparse(product.url).netloc.lower().removeprefix("www."),
            ean=product.ean,
            sku=product.sku,
            raw_data=dict(product.raw_data),
        )
