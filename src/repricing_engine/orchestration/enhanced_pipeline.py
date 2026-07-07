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

from repricing_engine.matching.classification import match_field_for
from repricing_engine.matching.scoring import select_best
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
    from repricing_engine.models.product import CatalogProduct, CompetitorProduct, MatchCandidate

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
        read_prices_on_fetch: bool = False,
        product_concurrency: int = _DEFAULT_PRODUCT_CONCURRENCY,
    ) -> None:
        """Create the orchestrator.

        Args:
            matching_pipeline: The synchronous matching pipeline (reused as-is).
            source_fetcher: Optional source fetcher (required for ``fetch``).
            pdp_verifier: Optional PDP verifier (required for ``verify_pdp`` or for
                reading real prices on ``fetch``).
            read_prices_on_fetch: When ``True``, ``--fetch`` runs each product's
                matched offers through the PDP verifier to read real price/stock.
            product_concurrency: Max catalog products processed concurrently.
        """
        self._matching = matching_pipeline
        self._source_fetcher = source_fetcher
        self._pdp_verifier = pdp_verifier
        self._read_prices_on_fetch = read_prices_on_fetch
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
        # Read real prices from discovered pages on --fetch (unless disabled), and
        # always when --verify-pdp is set. Both reuse the same PDP cascade.
        read_prices_on_fetch = fetch and settings.fetch_read_prices
        pdp_verifier = (
            PdpVerifier.from_settings(settings, client, ai_extractor=ai_extractor)
            if (verify_pdp or read_prices_on_fetch)
            else None
        )
        return cls(
            matching_pipeline,
            source_fetcher=source_fetcher,
            pdp_verifier=pdp_verifier,
            read_prices_on_fetch=read_prices_on_fetch,
        )

    async def run(
        self,
        catalog_products: list[CatalogProduct],
        *,
        csv_pool: list[CompetitorProduct],
        market: Market,
        fetch: bool = False,
        verify_pdp: bool = False,
        max_pdp_per_product: int = 0,
        strict_match: bool = False,
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
                    strict_match=strict_match,
                )
            advance()
            return result

        if not show_progress:
            results = await asyncio.gather(
                *(process(product, _noop) for product in catalog_products)
            )
            self._log_run_summary(results)
            return results

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
            results = await asyncio.gather(*coroutines)
        self._log_run_summary(results)
        return results

    @staticmethod
    def _log_run_summary(results: list[MatchResult]) -> None:
        """Log an aggregate view: offers per provider, matched products, priced offers."""
        per_provider: dict[str, int] = {}
        total_offers = 0
        priced_offers = 0
        matched_products = 0
        for result in results:
            if result.all_candidates:
                matched_products += 1
            for candidate in result.all_candidates:
                offer = candidate.competitor_product
                total_offers += 1
                provider = offer.source_provider or offer.source or "unknown"
                per_provider[provider] = per_provider.get(provider, 0) + 1
                if offer.price is not None:
                    priced_offers += 1
        breakdown = ", ".join(f"{name}={count}" for name, count in sorted(per_provider.items()))
        logger.info(
            "Fetch summary: %d/%d products matched, %d offers (%d priced). By source: %s",
            matched_products,
            len(results),
            total_offers,
            priced_offers,
            breakdown or "none",
        )

    async def _process_one(
        self,
        product: CatalogProduct,
        *,
        csv_pool: list[CompetitorProduct],
        market: Market,
        fetch: bool,
        verify_pdp: bool,
        max_pdp_per_product: int,
        strict_match: bool = False,
    ) -> MatchResult:
        """Fetch (optional) -> match (sync) -> verify/price (optional) for one product."""
        competitors = list(csv_pool)
        if fetch and self._source_fetcher is not None:
            competitors.extend(await self._source_fetcher.fetch_competitors(product, market))

        result = self._matching.match_one(product, competitors, full_landscape=fetch)

        # Read the real price/stock from each offer's page when verifying, or when
        # --fetch is configured to read prices (default). Same PDP cascade either way.
        read_prices = fetch and self._read_prices_on_fetch
        if (verify_pdp or read_prices) and self._pdp_verifier is not None:
            result = await self._pdp_verifier.verify_result(product, result, max_pdp_per_product)

        # Strict mode: keep only offers whose identity is *confirmed* to be this exact
        # catalog product (EAN/GTIN match, a confirmed SKU, or a PDP identifier hit) —
        # never mere title similarity. Done after PDP so a page that reveals the SKU
        # can still qualify.
        if strict_match:
            result = self._keep_identity_confirmed(product, result)
        return result

    @staticmethod
    def _keep_identity_confirmed(product: CatalogProduct, result: MatchResult) -> MatchResult:
        """Drop candidates not provably the same product as ``product``."""
        kept = [c for c in result.all_candidates if _identity_confirmed(c)]
        dropped = [c for c in result.all_candidates if not _identity_confirmed(c)]
        if dropped:
            logger.info(
                "Strict match %s: kept %d identifier-confirmed offer(s), dropped %d unconfirmed.",
                product.sku,
                len(kept),
                len(dropped),
            )
        return result.model_copy(
            update={
                "all_candidates": kept,
                "best_match": select_best(kept),
                "rejected_candidates": [*result.rejected_candidates, *dropped],
            }
        )


def _identity_confirmed(candidate: MatchCandidate) -> bool:
    """True when the offer is provably the catalog product, not a title-similarity guess.

    Confirmed when the match was established on an identifier (EAN/GTIN or a confirmed
    SKU — anything but ``snippet``) or when PDP verification found the catalog's
    EAN/GTIN/SKU on the actual product page (a ``PDP·…`` confirmation). An offer whose
    page carried a *different* barcode than the recovered consensus is rejected — the
    SKU alone matched but it's a different product/variant.
    """
    if candidate.match_details.get("gtin_conflict"):
        return False
    if match_field_for(candidate) != "snippet":
        return True
    confirmation = str(candidate.match_details.get("confirmation_method") or "")
    return confirmation.startswith("PDP·")


def _noop() -> None:
    """Progress callback used when progress display is disabled."""
