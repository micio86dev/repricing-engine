"""Internal data contracts for PDP (product-detail-page) verification."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from repricing_engine.models.enums import Availability


class FetchResult(BaseModel):
    """The outcome of fetching a product page."""

    model_config = ConfigDict(frozen=True)

    url: str
    html: str = ""
    status_code: int = 0
    fetch_method: str = "httpx"  # "httpx" | "playwright"
    response_time_ms: float = 0.0
    ok: bool = False  # True when usable HTML was retrieved


class IdentifierFindings(BaseModel):
    """Which catalog identifiers were located on a fetched page (free signals)."""

    model_config = ConfigDict(frozen=True)

    ean_found: bool = False
    gtin_found: bool = False
    sku_found: bool = False
    found_identifiers: list[str] = Field(default_factory=list)
    json_ld_data: dict[str, Any] = Field(default_factory=dict)
    # Price found in <meta>/microdata (not JSON-LD): a free fallback for pages that
    # expose the price only in OpenGraph/itemprop, so more offers get a real price.
    meta_price: str | None = None
    meta_currency: str | None = None
    # Shipping cost found in <meta> tags (not JSON-LD): a free fallback for pages
    # exposing shipping only in OpenGraph/product meta.
    meta_shipping: str | None = None

    @property
    def any_found(self) -> bool:
        """True when at least one identifier matched the page."""
        return self.ean_found or self.gtin_found or self.sku_found

    @property
    def has_json_ld(self) -> bool:
        """True when a Product JSON-LD block was parsed off the page."""
        return bool(self.json_ld_data)


class PdpExtractionResult(BaseModel):
    """Structured offer data extracted from a product page (regex/JSON-LD/AI)."""

    model_config = ConfigDict(frozen=True)

    price: Decimal | None = None
    currency: str | None = None
    shipping_cost: Decimal | None = None
    stock_quantity: int | None = None
    availability: Availability = Availability.UNKNOWN
    seller: str | None = None
    vat_included: bool | None = None  # schema.org valueAddedTaxIncluded, if stated
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
