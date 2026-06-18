"""EnhancedPipeline — fetch more competitors and verify them, around matching.

For each catalog product:

1. start from the CSV pool, optionally adding fetched competitors (``--fetch``);
2. run the existing synchronous matching cascade inline;
3. optionally verify the matched candidates against their product pages
   (``--verify-pdp``).

Cross-product work runs concurrently under a bounded semaphore; results are
returned in input order.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
)

from repricing_engine.pdp.extractor import build_ai_extractor
from repricing_engine.pdp.verifier import PdpVerifier
from repricing_engine.sources.orchestrator import SourceFetcher

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import httpx

    from repricing_engine.config import Settings
    from repricing_engine.matching.pipeline import MatchingPipeline
    from repricing_engine.models.enums import Market
    from repricing_engine.models.match_result import MatchResult
    from repricing_engine.models.product import CatalogProduct, CompetitorProduct

logger = logging.getLogger(__name__)

_DEFAULT_PRODUCT_CONCURRENCY = 4


class EnhancedPipeline:
    """Orchestrate fetch -> match -> verify across catalog products."""

    def __init__(
        self,
        matching_pipeline: MatchingPipeline,
        *,
        source_fetcher: SourceFetcher | None = None,
        pdp_verifier: PdpVerifier | None = None,
        product_concurrency: int = _DEFAULT_PRODUCT_CONCURRENCY,
    ) -> None:
        """Create the orchestrator.

        Args:
            matching_pipeline: The synchronous matching pipeline (reused as-is).
            source_fetcher: Optional source fetcher (required for ``fetch``).
            pdp_verifier: Optional PDP verifier (required for ``verify_pdp``).
            product_concurrency: Max catalog products processed concurrently.
        """
        self._matching = matching_pipeline
        self._source_fetcher = source_fetcher
        self._pdp_verifier = pdp_verifier
        self._product_concurrency = max(1, product_concurrency)

    @classmethod
    def build(
        cls,
        settings: Settings,
        client: httpx.AsyncClient,
        *,
        matching_pipeline: MatchingPipeline,
        fetch: bool,
        verify_pdp: bool,
    ) -> EnhancedPipeline:
        """Wire the fetcher/verifier from settings, sharing one Groq extractor.

        Args:
            settings: Engine settings.
            client: Shared async HTTP client (its lifecycle is owned by the caller).
            matching_pipeline: The matching pipeline to reuse.
            fetch: Whether to build a source fetcher.
            verify_pdp: Whether to build a PDP verifier.
        """
        ai_extractor = build_ai_extractor(settings) if (fetch or verify_pdp) else None
        source_fetcher = (
            SourceFetcher.from_settings(settings, client, ai_extractor=ai_extractor)
            if fetch
            else None
        )
        pdp_verifier = (
            PdpVerifier.from_settings(settings, client, ai_extractor=ai_extractor)
            if verify_pdp
            else None
        )
        return cls(
            matching_pipeline,
            source_fetcher=source_fetcher,
            pdp_verifier=pdp_verifier,
        )

    async def run(
        self,
        catalog_products: list[CatalogProduct],
        *,
        csv_pool: list[CompetitorProduct],
        market: Market,
        fetch: bool = False,
        verify_pdp: bool = False,
        max_pdp_per_product: int = 15,
        show_progress: bool = True,
    ) -> list[MatchResult]:
        """Process every catalog product, returning results in input order."""
        semaphore = asyncio.Semaphore(self._product_concurrency)

        async def process(product: CatalogProduct, advance: Callable[[], None]) -> MatchResult:
            async with semaphore:
                result = await self._process_one(
                    product,
                    csv_pool=csv_pool,
                    market=market,
                    fetch=fetch,
                    verify_pdp=verify_pdp,
                    max_pdp_per_product=max_pdp_per_product,
                )
            advance()
            return result

        if not show_progress:
            return await asyncio.gather(*(process(product, _noop) for product in catalog_products))

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
        ) as progress:
            task_id = progress.add_task("Fetch / match / verify", total=len(catalog_products))

            def advance() -> None:
                progress.advance(task_id)

            coroutines: list[Awaitable[MatchResult]] = [
                process(product, advance) for product in catalog_products
            ]
            return await asyncio.gather(*coroutines)

    async def _process_one(
        self,
        product: CatalogProduct,
        *,
        csv_pool: list[CompetitorProduct],
        market: Market,
        fetch: bool,
        verify_pdp: bool,
        max_pdp_per_product: int,
    ) -> MatchResult:
        """Fetch (optional) -> match (sync) -> verify (optional) for one product."""
        competitors = list(csv_pool)
        if fetch and self._source_fetcher is not None:
            competitors.extend(await self._source_fetcher.fetch_competitors(product, market))

        result = self._matching.match_one(product, competitors)

        if verify_pdp and self._pdp_verifier is not None:
            result = await self._pdp_verifier.verify_result(product, result, max_pdp_per_product)
        return result


def _noop() -> None:
    """Progress callback used when progress display is disabled."""
