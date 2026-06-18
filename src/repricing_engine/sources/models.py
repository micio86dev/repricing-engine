"""Internal data contracts for the source-fetching layer.

A :class:`RawSearchResult` is the provider-agnostic shape every provider returns;
:func:`~repricing_engine.sources.mapper.to_competitor_product` converts it into the
engine's :class:`~repricing_engine.models.product.CompetitorProduct`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RawSearchResult(BaseModel):
    """A single offer/result returned by a source provider (pre-normalization)."""

    model_config = ConfigDict(frozen=True)

    source_provider: str  # which provider produced this result (searxng, ...)
    title: str
    url: str
    snippet: str | None = None
    price: Decimal | None = None  # often unknown from a SERP snippet
    currency: str | None = None
    shipping_cost: Decimal | None = None
    domain: str | None = None  # bare host (without leading www.)
    ean: str | None = None
    sku: str | None = None
    raw_data: dict[str, Any] = Field(default_factory=dict)


class ProviderConfig(BaseModel):
    """Per-provider runtime configuration, derived from :class:`Settings`."""

    enabled: bool = True
    base_url: str | None = None
    rate_limit_seconds: float = Field(default=0.0, ge=0.0)
    timeout_seconds: float = Field(default=10.0, gt=0.0)
