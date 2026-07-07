"""SourceFetcher — run enabled providers concurrently and dedupe their results.

Providers are ordered cheapest-first and queried with :func:`asyncio.gather`;
each call is guarded so one provider's failure can't sink the batch. The merged
raw results are deduplicated (URL then domain) and mapped to competitor products.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from repricing_engine.exceptions import SourceFetchError
from repricing_engine.sources.deduplicator import deduplicate
from repricing_engine.sources.enrichment import confirm_identifiers
from repricing_engine.sources.mapper import to_competitor_product
from repricing_engine.sources.providers.dataforseo import DataForSeoProvider
from repricing_engine.sources.providers.duckduckgo import DuckDuckGoProvider
from repricing_engine.sources.providers.ebay import EbaySourceProvider
from repricing_engine.sources.providers.feed import FeedProvider
from repricing_engine.sources.providers.keepa import KeepaProvider
from repricing_engine.sources.providers.searxng import SearXNGProvider
from repricing_engine.sources.providers.serper import SerperProvider
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
                max_pages=settings.searxng_max_pages,
                max_concurrency=settings.searxng_max_concurrency,
                rate_limit_seconds=settings.searxng_rate_limit_seconds,
            ),
            TrovaPrezziProvider(
                client,
                enabled=settings.trovaprezzi_enabled,
                timeout_seconds=settings.pdp_fetch_timeout_seconds,
                rate_limit_seconds=settings.trovaprezzi_rate_limit_seconds,
                ai_extractor=ai_extractor,
            ),
            DuckDuckGoProvider(
                client,
                enabled=settings.duckduckgo_enabled,
                rate_limit_seconds=settings.duckduckgo_rate_limit_seconds,
                timeout_seconds=settings.pdp_fetch_timeout_seconds,
            ),
        ]
        # eBay is only wired in when explicitly enabled *and* both OAuth credentials
        # are present, so the default free path stays inert without any keys.
        if settings.ebay_enabled and settings.ebay_client_id and settings.ebay_client_secret:
            providers.append(
                EbaySourceProvider(
                    client,
                    settings.ebay_client_id,
                    settings.ebay_client_secret,
                    timeout_seconds=settings.pdp_fetch_timeout_seconds,
                )
            )
        if (
            settings.dataforseo_enabled
            and settings.dataforseo_login
            and settings.dataforseo_password
        ):
            providers.append(
                DataForSeoProvider(
                    client,
                    settings.dataforseo_login,
                    settings.dataforseo_password,
                    mode=settings.dataforseo_mode,
                    timeout_seconds=settings.pdp_fetch_timeout_seconds,
                )
            )
        if settings.serper_enabled and settings.serper_api_key:
            providers.append(
                SerperProvider(
                    client,
                    settings.serper_api_key,
                    timeout_seconds=settings.pdp_fetch_timeout_seconds,
                )
            )
        if settings.keepa_enabled and settings.keepa_api_key:
            providers.append(
                KeepaProvider(
                    client,
                    settings.keepa_api_key,
                    timeout_seconds=settings.pdp_fetch_timeout_seconds,
                )
            )
        if settings.feed_urls:
            providers.append(
                FeedProvider(
                    client,
                    [u.strip() for u in settings.feed_urls.split(",") if u.strip()],
                    timeout_seconds=settings.pdp_fetch_timeout_seconds,
                )
            )
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
        # Truthfully stamp catalog SKU/EAN/brand where a result's text confirms it,
        # so deterministic layers (not just semantic) can match fetched offers.
        enriched = [confirm_identifiers(result, product) for result in raw]
        deduped = deduplicate(enriched)
        competitors = [to_competitor_product(result, market) for result in deduped]

        priced = sum(1 for c in competitors if c.price is not None)
        logger.info(
            "Fetch %s: %d raw result(s) -> %d offer(s) (one per domain), %d priced.",
            product.sku,
            len(raw),
            len(competitors),
            priced,
        )
        return competitors

    @staticmethod
    async def _safe_search(
        provider: BaseSourceProvider,
        product: CatalogProduct,
        market: Market,
    ) -> list[RawSearchResult]:
        """Run one provider, degrading a failure to an empty result + a log line."""
        try:
            results = await provider.search(product, market)
        except SourceFetchError as exc:
            # Expected provider-level failure (block/rate-limit/timeout): concise, no traceback.
            logger.warning(
                "Source provider %r unavailable for %s: %s", provider.name, product.sku, exc
            )
            return []
        except Exception:
            # Truly unexpected — keep the full traceback for debugging.
            logger.warning("Source provider %r failed; skipping.", provider.name, exc_info=True)
            return []
        logger.info(
            "Provider %s returned %d result(s) for %s.", provider.name, len(results), product.sku
        )
        return results
