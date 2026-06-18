"""Abstract base class for source providers.

A provider knows one way to *discover* competitor offers for a catalog product
(a metasearch engine, a price-comparison site, a SERP scraper, a CSV export).
Providers are injected with an :class:`httpx.AsyncClient` so tests can supply an
``httpx.MockTransport`` and never touch the network.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repricing_engine.models.enums import Market
    from repricing_engine.models.product import CatalogProduct
    from repricing_engine.sources.models import RawSearchResult

#: Cost tiers, used to order providers (cheapest first).
COST_FREE = 0
COST_CHEAP = 1
COST_EXPENSIVE = 2

#: A single realistic desktop User-Agent for provider HTTP requests.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class BaseSourceProvider(ABC):
    """A single way to discover competitor offers for a catalog product."""

    #: Human-readable provider name, surfaced as ``source`` / ``source_provider``.
    name: str = "base"
    #: Relative cost of querying this provider (0 free, 1 cheap, 2 expensive).
    cost_tier: int = COST_FREE

    @abstractmethod
    async def search(
        self,
        product: CatalogProduct,
        market: Market,
    ) -> list[RawSearchResult]:
        """Return raw search results for ``product`` in ``market`` (never ``None``)."""

    def is_available(self) -> bool:
        """Whether this provider is configured and may be queried.

        Defaults to ``True``; providers needing configuration (e.g. a base URL)
        override this and log a one-line setup hint when unavailable.
        """
        return True
