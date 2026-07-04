"""The matching pipeline: orchestrates the cascade of layers per product."""

from __future__ import annotations

import logging
from time import perf_counter
from typing import TYPE_CHECKING

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
)

from repricing_engine.matching.layers.ai_quality_gate import AiQualityGateLayer
from repricing_engine.matching.layers.exact_id import ExactIdLayer
from repricing_engine.matching.layers.semantic import SemanticLayer
from repricing_engine.matching.layers.sku_brand import SkuBrandLayer
from repricing_engine.matching.scoring import dedupe_candidates, select_best
from repricing_engine.models.match_result import MatchResult

if TYPE_CHECKING:
    from repricing_engine.config import Settings
    from repricing_engine.matching.layers.base import BaseMatchLayer
    from repricing_engine.matching.layers.semantic import TextEncoder
    from repricing_engine.models.product import (
        CatalogProduct,
        CompetitorProduct,
        MatchCandidate,
    )

logger = logging.getLogger(__name__)

# Skip the (expensive) semantic layer when a cheaper layer is already confident.
_SEMANTIC_SKIP_CONFIDENCE = 0.90


class MatchingPipeline:
    """Run the Layer 1 -> 4 cascade and produce a :class:`MatchResult` per product."""

    def __init__(
        self,
        settings: Settings,
        *,
        skip_ai: bool = False,
        min_confidence: float = 0.60,
        encoder: TextEncoder | None = None,
        ai_gate: AiQualityGateLayer | None = None,
        layers: list[BaseMatchLayer] | None = None,
    ) -> None:
        """Create the pipeline.

        Args:
            settings: Engine settings (thresholds, model names, Groq key).
            skip_ai: When ``True``, the AI quality gate is not run.
            min_confidence: Minimum confidence for a candidate to be retained.
            encoder: Optional injected sentence encoder for the semantic layer.
            ai_gate: Optional injected AI quality gate (overrides auto-creation).
            layers: Optional explicit list of matching layers (overrides defaults).
        """
        self.settings = settings
        self.min_confidence = min_confidence
        self.layers = layers or [
            ExactIdLayer(),
            SkuBrandLayer(),
            SemanticLayer(model_name=settings.embedding_model, encoder=encoder),
        ]
        self.ai_gate = None if skip_ai else (ai_gate or self._build_ai_gate(settings))

    def run(
        self,
        catalog_products: list[CatalogProduct],
        competitor_products: list[CompetitorProduct],
        *,
        show_progress: bool = True,
    ) -> list[MatchResult]:
        """Match every catalog product against the competitor products."""
        if not show_progress:
            return [self.match_one(p, competitor_products) for p in catalog_products]

        results: list[MatchResult] = []
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task("Matching products", total=len(catalog_products))
            for product in catalog_products:
                results.append(self.match_one(product, competitor_products))
                progress.advance(task)
        return results

    def match_one(
        self,
        catalog_product: CatalogProduct,
        competitor_products: list[CompetitorProduct],
        *,
        full_landscape: bool = False,
    ) -> MatchResult:
        """Run the cascade for a single catalog product.

        Args:
            catalog_product: The product being priced.
            competitor_products: Candidate competitor offers to match against.
            full_landscape: When ``True`` (the ``--fetch`` path), the semantic
                layer still scores every competitor that isn't already confidently
                matched, so discovered offers are never suppressed by a strong
                match elsewhere. When ``False`` (the default CSV-only path) the
                legacy global semantic-skip is preserved byte-for-byte.
        """
        start = perf_counter()

        raw_candidates = self._collect_candidates(
            catalog_product, competitor_products, full_landscape=full_landscape
        )
        deduped = dedupe_candidates(raw_candidates)

        accepted = [c for c in deduped if c.confidence >= self.min_confidence]
        rejected = [c for c in deduped if c.confidence < self.min_confidence]

        if self.ai_gate is not None and accepted:
            accepted, ai_rejected = self._apply_ai_gate(catalog_product, accepted)
            rejected.extend(ai_rejected)

        accepted.sort(key=lambda c: c.confidence, reverse=True)
        elapsed_ms = (perf_counter() - start) * 1000.0

        return MatchResult(
            catalog_product=catalog_product,
            best_match=select_best(accepted),
            all_candidates=accepted,
            rejected_candidates=rejected,
            processing_time_ms=round(elapsed_ms, 3),
        )

    def _collect_candidates(
        self,
        catalog_product: CatalogProduct,
        competitor_products: list[CompetitorProduct],
        *,
        full_landscape: bool = False,
    ) -> list[MatchCandidate]:
        """Run non-AI layers in cascade order, skipping semantic when confident."""
        collected: list[MatchCandidate] = []
        for layer in self.layers:
            if layer.name == "semantic":
                targets = self._semantic_targets(
                    catalog_product, competitor_products, collected, full_landscape=full_landscape
                )
                if not targets:
                    continue
                collected.extend(layer.match(catalog_product, targets))
            else:
                collected.extend(layer.match(catalog_product, competitor_products))
        return collected

    def _semantic_targets(
        self,
        catalog_product: CatalogProduct,
        competitor_products: list[CompetitorProduct],
        collected: list[MatchCandidate],
        *,
        full_landscape: bool,
    ) -> list[CompetitorProduct]:
        """Decide which competitors the semantic layer should still score.

        CSV-only path (``full_landscape=False``): legacy behavior — skip semantic
        entirely once any candidate is already confident. Landscape path
        (``full_landscape=True``): score every competitor that isn't itself
        already confidently matched, so discovered offers survive.
        """
        if not full_landscape:
            if self._already_confident(collected):
                logger.debug("Skipping semantic for %s (already confident)", catalog_product.sku)
                return []
            return competitor_products

        confident_ids = {
            c.competitor_product.source_id
            for c in collected
            if c.confidence >= _SEMANTIC_SKIP_CONFIDENCE
        }
        remaining = [c for c in competitor_products if c.source_id not in confident_ids]
        if not remaining:
            logger.debug(
                "Skipping semantic for %s (all competitors confident)", catalog_product.sku
            )
        return remaining

    @staticmethod
    def _already_confident(candidates: list[MatchCandidate]) -> bool:
        """True when an existing candidate already clears the semantic-skip bar."""
        return any(c.confidence >= _SEMANTIC_SKIP_CONFIDENCE for c in candidates)

    def _apply_ai_gate(
        self,
        catalog_product: CatalogProduct,
        candidates: list[MatchCandidate],
    ) -> tuple[list[MatchCandidate], list[MatchCandidate]]:
        """Run the AI gate and split into (accepted, rejected)."""
        reviewed = self.ai_gate.review(catalog_product, candidates)
        accepted: list[MatchCandidate] = []
        rejected: list[MatchCandidate] = []
        for candidate in reviewed:
            ai_is_match = candidate.match_details.get("ai_is_match", True)
            if ai_is_match and candidate.confidence >= self.min_confidence:
                accepted.append(candidate)
            else:
                rejected.append(candidate)
        return accepted, rejected

    @staticmethod
    def _build_ai_gate(settings: Settings) -> AiQualityGateLayer | None:
        """Build a Groq-backed gate from settings, or ``None`` if unavailable."""
        if not settings.groq_api_key:
            logger.warning("No GROQ_API_KEY set — AI quality gate disabled.")
            return None
        try:
            from groq import Groq

            client = Groq(api_key=settings.groq_api_key)
        except Exception as exc:
            logger.warning("Could not initialize Groq client — AI gate disabled (%s)", exc)
            return None
        return AiQualityGateLayer(client, model=settings.groq_model)
