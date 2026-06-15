"""Ingestor for the client's own product catalog CSV."""

from decimal import Decimal
from pathlib import Path

from repricing_engine.exceptions import IngestionError, NormalizationError
from repricing_engine.ingestion.base import BaseIngestor, read_csv_rows
from repricing_engine.models.enums import Market
from repricing_engine.models.product import CatalogProduct
from repricing_engine.normalization.identifiers import (
    normalize_ean,
    normalize_gtin,
    normalize_sku,
)
from repricing_engine.normalization.price import normalize_price


class CatalogIngestor(BaseIngestor[CatalogProduct]):
    """Parse a client catalog CSV into :class:`CatalogProduct` instances.

    Column names are matched case-insensitively against ``COLUMN_MAP``. Any
    unmapped columns are preserved in ``attributes`` so nothing is lost.
    """

    # Logical field -> accepted source column names (lower-cased).
    COLUMN_MAP: dict[str, tuple[str, ...]] = {
        "sku": ("sku", "product_sku", "code"),
        "ean": ("ean", "ean13", "barcode"),
        "gtin": ("gtin",),
        "brand": ("brand", "manufacturer", "marca"),
        "title": ("title", "name", "product_name", "titolo"),
        "category": ("category", "categoria", "cat"),
        "market": ("market", "country", "mercato"),
        "price": ("price", "our_price", "sell_price", "prezzo"),
        "cogs": ("cogs", "cost", "our_cogs", "costo"),
        "currency": ("currency", "valuta"),
    }

    def __init__(self, default_market: Market = Market.IT) -> None:
        """Create the ingestor.

        Args:
            default_market: Market used when a row has no market column/value.
        """
        self.default_market = default_market

    def ingest(self, file_path: Path) -> list[CatalogProduct]:
        """Read and normalize the catalog CSV.

        Args:
            file_path: Path to the catalog CSV.

        Returns:
            A list of normalized catalog products.

        Raises:
            IngestionError: If a required field (sku/title/brand) is missing.
        """
        rows = read_csv_rows(file_path)
        products: list[CatalogProduct] = []

        for index, row in enumerate(rows):
            lowered = {key.lower(): value for key, value in row.items()}
            mapped_keys = self._collect_mapped_keys(lowered)

            sku = normalize_sku(self._first(lowered, "sku"))
            title = (self._first(lowered, "title") or "").strip()
            brand = (self._first(lowered, "brand") or "").strip()
            if not sku or not title or not brand:
                msg = f"Catalog row {index + 1} missing required sku/title/brand"
                raise IngestionError(msg)

            attributes = {
                key: value
                for key, value in row.items()
                if key.lower() not in mapped_keys and value != ""
            }

            market = self._resolve_market(self._first(lowered, "market"))
            currency = (self._first(lowered, "currency") or "").strip().upper() or None
            products.append(
                CatalogProduct(
                    sku=sku,
                    ean=normalize_ean(self._first(lowered, "ean")),
                    gtin=normalize_gtin(self._first(lowered, "gtin")),
                    brand=brand,
                    title=title,
                    category=(self._first(lowered, "category") or "").strip(),
                    market=market,
                    price=self._money(lowered, "price", currency or "EUR", market),
                    cogs=self._money(lowered, "cogs", currency or "EUR", market),
                    currency=currency,
                    attributes=attributes,
                )
            )
        return products

    def _money(
        self, row: dict[str, str], field: str, currency: str, market: Market
    ) -> Decimal | None:
        """Parse an optional money field, returning ``None`` if absent/unparseable."""
        raw = self._first(row, field)
        if raw is None:
            return None
        try:
            return normalize_price(raw, currency, market)
        except NormalizationError:
            return None

    def _first(self, row: dict[str, str], field: str) -> str | None:
        """Return the first present source value for a logical ``field``."""
        for candidate in self.COLUMN_MAP[field]:
            if candidate in row and row[candidate] != "":
                return row[candidate]
        return None

    def _collect_mapped_keys(self, row: dict[str, str]) -> set[str]:
        """Return the set of source column names that map to known fields."""
        mapped: set[str] = set()
        for candidates in self.COLUMN_MAP.values():
            mapped.update(c for c in candidates if c in row)
        return mapped

    def _resolve_market(self, value: str | None) -> Market:
        """Resolve a market code, falling back to ``default_market``."""
        if not value:
            return self.default_market
        try:
            return Market(value.strip().upper())
        except ValueError:
            return self.default_market
