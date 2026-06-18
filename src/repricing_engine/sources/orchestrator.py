"""SourceFetcher — run enabled providers concurrently and dedupe their results.

Providers are ordered cheapest-first and queried with :func:`asyncio.gather`;
each call is guarded so one provider's failure can't sink the batch. The merged
raw results are deduplicated (URL then domain) and mapped to competitor products.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from repricing_engine.sources.deduplicator import deduplicate
from repricing_engine.sources.mapper import to_competitor_product
from repricing_engine.sources.providers.duckduckgo import DuckDuckGoProvider
from repricing_engine.sources.providers.searxng import SearXNGProvider
from repricing_engine.sources.providers.trovaprezzi import TrovaPrezziProvider

if TYPE_CHECKING:
    import httpx

    from repricing_engine.config import Settings
    from repricing_engine.models.enums import Market
    from repricing_engine.models.product import CatalogProduct, CompetitorProduct
    from repricing_engine.pdp.extractor import AiExtractor
    from repricing_engine.sources.base import BaseSourceProvider
    from repricing_engine.sources.models import RawSearchResult

logger = logging.getLogger(__name__)


class SourceFetcher:
    """Aggregate competitor offers from several providers, then deduplicate."""

    def __init__(self, providers: list[BaseSourceProvider]) -> None:
        """Create the fetcher.

        Args:
            providers: The providers to query (ordered cheapest-first internally).
        """
        self._providers = sorted(providers, key=lambda p: p.cost_tier)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        client: httpx.AsyncClient,
        *,
        ai_extractor: AiExtractor | None = None,
    ) -> SourceFetcher:
        """Build the default online provider set from settings.

        Args:
            settings: Engine settings (provider toggles, URLs, rate limits).
            client: Shared async HTTP client injected into every provider.
            ai_extractor: Optional Groq extractor for the TrovaPrezzi fallback.

        Returns:
            A configured :class:`SourceFetcher` (the CSV provider is *not*
            included here — the CSV pool is supplied separately by the caller).
        """
        providers: list[BaseSourceProvider] = [
            SearXNGProvider(
                client,
                settings.searxng_base_url,
                enabled=settings.searxng_enabled,
                timeout_seconds=settings.pdp_fetch_timeout_seconds,
            ),
            TrovaPrezziProvider(
                client,
                enabled=settings.trovaprezzi_enabled,
                timeout_seconds=settings.pdp_fetch_timeout_seconds,
                ai_extractor=ai_extractor,
            ),
            DuckDuckGoProvider(
                client,
                enabled=settings.duckduckgo_enabled,
                rate_limit_seconds=settings.duckduckgo_rate_limit_seconds,
                timeout_seconds=settings.pdp_fetch_timeout_seconds,
            ),
        ]
        return cls(providers)

    @property
    def providers(self) -> list[BaseSourceProvider]:
        """The configured providers (cheapest-first)."""
        return list(self._providers)

    async def fetch_competitors(
        self,
        product: CatalogProduct,
        market: Market,
    ) -> list[CompetitorProduct]:
        """Fetch, dedupe, and map competitor offers for a single product.

        Returns:
            One :class:`CompetitorProduct` per distinct retailer domain. Empty
            when no provider is available or none returns a usable result.
        """
        active = [p for p in self._providers if p.is_available()]
        if not active:
            logger.info("No source providers available; returning no fetched competitors.")
            return []

        gathered = await asyncio.gather(
            *(self._safe_search(provider, product, market) for provider in active),
            return_exceptions=False,
        )
        raw: list[RawSearchResult] = [result for batch in gathered for result in batch]
        deduped = deduplicate(raw)
        return [to_competitor_product(result, market) for result in deduped]

    @staticmethod
    async def _safe_search(
        provider: BaseSourceProvider,
        product: CatalogProduct,
        market: Market,
    ) -> list[RawSearchResult]:
        """Run one provider, degrading a failure to an empty result + a log line."""
        try:
            return await provider.search(product, market)
        except Exception:
            logger.warning("Source provider %r failed; skipping.", provider.name, exc_info=True)
            return []
