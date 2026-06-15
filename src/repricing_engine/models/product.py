"""Pydantic data contracts for catalog / competitor products and match candidates."""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from repricing_engine.models.enums import Market, MatchMethod


class CatalogProduct(BaseModel):
    """A product from the client's own catalog (the thing we want to price)."""

    model_config = ConfigDict(frozen=True)

    sku: str
    brand: str
    title: str
    category: str
    market: Market
    ean: str | None = None
    gtin: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class CompetitorProduct(BaseModel):
    """A product offered by a competitor (from OxyLabs or another source)."""

    model_config = ConfigDict(frozen=True)

    source: str
    source_id: str
    title: str
    price: Decimal
    currency: str
    url: str
    market: Market
    ean: str | None = None
    gtin: str | None = None
    sku: str | None = None
    brand: str | None = None
    shipping_cost: Decimal | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)


class MatchCandidate(BaseModel):
    """A possible match between a catalog product and a competitor product."""

    competitor_product: CompetitorProduct
    confidence: float = Field(ge=0.0, le=1.0)
    match_method: MatchMethod
    layer_source: str
    match_details: dict[str, Any] = Field(default_factory=dict)
