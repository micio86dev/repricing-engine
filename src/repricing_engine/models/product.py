"""Pydantic data contracts for catalog / competitor products and match candidates."""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from repricing_engine.models.enums import Availability, Market, MatchMethod


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
    price: Decimal | None = None  # our selling price (for position / suggested_price)
    cogs: Decimal | None = None  # our cost of goods sold (price floor)
    currency: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class CompetitorProduct(BaseModel):
    """A product offered by a competitor (from OxyLabs or another source)."""

    model_config = ConfigDict(frozen=True)

    source: str
    source_id: str
    title: str
    price: Decimal | None = None  # may be unknown until a PDP is fetched/verified
    currency: str
    url: str
    market: Market
    ean: str | None = None
    gtin: str | None = None
    sku: str | None = None
    brand: str | None = None
    shipping_cost: Decimal | None = None
    availability: Availability = Availability.UNKNOWN
    seller: str | None = None  # competitor/retailer display name
    source_provider: str | None = None  # the fetch provider (searxng, trovaprezzi, ...)
    scraped_at: str | None = None  # when this offer was captured
    raw_data: dict[str, Any] = Field(default_factory=dict)


class MatchCandidate(BaseModel):
    """A possible match between a catalog product and a competitor product."""

    competitor_product: CompetitorProduct
    confidence: float = Field(ge=0.0, le=1.0)
    match_method: MatchMethod
    layer_source: str
    match_details: dict[str, Any] = Field(default_factory=dict)
