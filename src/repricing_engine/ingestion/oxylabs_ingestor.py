"""Ingestor for an OxyLabs competitor-pricing export CSV.

The column mapping is intentionally a simple, editable dict: when the real
OxyLabs sample arrives, adjust ``COLUMN_MAP`` without touching the parsing
logic.
"""

from pathlib import Path

from repricing_engine.exceptions import IngestionError
from repricing_engine.ingestion.base import BaseIngestor, read_csv_rows
from repricing_engine.models.enums import Market
from repricing_engine.models.product import CompetitorProduct
from repricing_engine.normalization.identifiers import (
    normalize_ean,
    normalize_gtin,
    normalize_sku,
)
from repricing_engine.normalization.price import extract_shipping, normalize_price


class OxyLabsIngestor(BaseIngestor[CompetitorProduct]):
    """Parse an OxyLabs export CSV into :class:`CompetitorProduct` instances."""

    SOURCE_NAME = "oxylabs"

    # Logical field -> accepted OxyLabs column names (lower-cased).
    COLUMN_MAP: dict[str, tuple[str, ...]] = {
        "source_id": ("product_id", "id", "asin", "result_id"),
        "title": ("title", "product_title", "name"),
        "brand": ("brand", "manufacturer"),
        "ean": ("ean", "barcode"),
        "gtin": ("gtin",),
        "sku": ("sku", "seller_sku"),
        "price": ("price", "price_value", "current_price"),
        "currency": ("currency", "price_currency"),
        "url": ("url", "product_url", "link"),
    }
    DEFAULT_CURRENCY = "EUR"

    def __init__(self, default_market: Market = Market.IT) -> None:
        """Create the ingestor.

        Args:
            default_market: Market assumed for rows without a market column.
        """
        self.default_market = default_market

    def ingest(self, file_path: Path) -> list[CompetitorProduct]:
        """Read and normalize the OxyLabs export CSV.

        Args:
            file_path: Path to the OxyLabs export CSV.

        Returns:
            A list of normalized competitor products.

        Raises:
            IngestionError: If a row lacks a usable title or price.
        """
        rows = read_csv_rows(file_path)
        products: list[CompetitorProduct] = []

        for index, row in enumerate(rows):
            lowered = {key.lower(): value for key, value in row.items()}

            title = (self._first(lowered, "title") or "").strip()
            raw_price = self._first(lowered, "price")
            if not title or raw_price is None:
                msg = f"OxyLabs row {index + 1} missing required title/price"
                raise IngestionError(msg)

            currency = (self._first(lowered, "currency") or self.DEFAULT_CURRENCY).strip().upper()
            market = self.default_market
            source_id = self._first(lowered, "source_id") or f"row-{index + 1}"

            products.append(
                CompetitorProduct(
                    source=self.SOURCE_NAME,
                    source_id=source_id,
                    title=title,
                    price=normalize_price(raw_price, currency, market),
                    currency=currency,
                    url=(self._first(lowered, "url") or "").strip(),
                    market=market,
                    ean=normalize_ean(self._first(lowered, "ean")),
                    gtin=normalize_gtin(self._first(lowered, "gtin")),
                    sku=self._normalized_sku(self._first(lowered, "sku")),
                    brand=self._clean_brand(self._first(lowered, "brand")),
                    shipping_cost=extract_shipping(lowered, self.SOURCE_NAME),
                    raw_data=dict(row),
                )
            )
        return products

    def _first(self, row: dict[str, str], field: str) -> str | None:
        """Return the first present source value for a logical ``field``."""
        for candidate in self.COLUMN_MAP[field]:
            if candidate in row and row[candidate] != "":
                return row[candidate]
        return None

    @staticmethod
    def _normalized_sku(value: str | None) -> str | None:
        """Normalize an optional SKU, preserving ``None`` for absent values."""
        if not value:
            return None
        return normalize_sku(value)

    @staticmethod
    def _clean_brand(value: str | None) -> str | None:
        """Strip an optional brand, preserving ``None`` for absent values."""
        if not value or not value.strip():
            return None
        return value.strip()
