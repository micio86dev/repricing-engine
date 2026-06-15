"""Regression tests over the "dirty" fixtures (offline, AI gate stubbed).

These lock in the project's core principle — *never trust a single identifier* —
on realistic messy data where competitors share a product's EAN but are actually
a different item (an accessory). The deterministic cascade is fooled by the
shared EAN; the AI quality gate (Layer 4) is what rejects those false positives.

Both the sentence encoder and the Groq client are dependency-injected test
doubles, so nothing here downloads a model or hits the network.
"""

from pathlib import Path

from repricing_engine.ingestion.catalog_ingestor import CatalogIngestor
from repricing_engine.ingestion.oxylabs_ingestor import OxyLabsIngestor
from repricing_engine.matching.layers.ai_quality_gate import AiQualityGateLayer
from repricing_engine.matching.pipeline import MatchingPipeline
from repricing_engine.models.match_result import MatchResult
from tests.conftest import PredicateGroqClient

# Words that flag a listing as an accessory rather than the product itself.
_ACCESSORY_HINTS = ("cover", "custodia", "case", "pellicola", "vetro", "bundle", "protector")


def _looks_like_real_product(prompt: str) -> bool:
    """Stand-in AI verdict: reject candidates whose listing reads as an accessory."""
    return not any(hint in prompt.lower() for hint in _ACCESSORY_HINTS)


def _by_sku(results: list[MatchResult]) -> dict[str, MatchResult]:
    return {r.catalog_product.sku: r for r in results}


class TestDirtyDataRegression:
    def test_ean_trap_accepted_without_ai_gate(
        self,
        dirty_catalog_path: Path,
        dirty_oxylabs_path: Path,
        settings,
        fake_encoder,
    ):
        """Without the AI gate, a screen protector sharing the phone's EAN is matched.

        This documents the *false positive* the AI gate exists to catch: the only
        competitor carrying the Galaxy S22 EAN is a "Generic" screen protector.
        """
        catalog = CatalogIngestor().ingest(dirty_catalog_path)
        competitors = OxyLabsIngestor().ingest(dirty_oxylabs_path)
        pipeline = MatchingPipeline(settings, skip_ai=True, encoder=fake_encoder)

        s22 = _by_sku(pipeline.run(catalog, competitors, show_progress=False))["SAM-GAL-S22"]

        assert s22.is_matched
        assert "pellicola" in s22.best_match.competitor_product.title.lower()

    def test_ai_gate_rejects_ean_trap_accessories(
        self,
        dirty_catalog_path: Path,
        dirty_oxylabs_path: Path,
        settings,
        fake_encoder,
    ):
        """With the AI gate, accessories sharing a product's EAN are rejected."""
        catalog = CatalogIngestor().ingest(dirty_catalog_path)
        competitors = OxyLabsIngestor().ingest(dirty_oxylabs_path)
        gate = AiQualityGateLayer(PredicateGroqClient(_looks_like_real_product))
        pipeline = MatchingPipeline(settings, skip_ai=False, ai_gate=gate, encoder=fake_encoder)

        by_sku = _by_sku(pipeline.run(catalog, competitors, show_progress=False))

        # Galaxy S22: its only same-EAN listing is a screen protector -> the gate
        # rejects it, and it must not survive as the best match.
        s22 = by_sku["SAM-GAL-S22"]
        ai_rejected = [
            c for c in s22.rejected_candidates if c.match_details.get("ai_is_match") is False
        ]
        assert any("pellicola" in c.competitor_product.title.lower() for c in ai_rejected)
        if s22.is_matched:
            assert "pellicola" not in s22.best_match.competitor_product.title.lower()

        # iPhone 14: the genuine listing still wins, but the same-EAN cover that
        # the cascade also surfaced is rejected by the gate.
        iphone = by_sku["APL-IPH14-128"]
        assert iphone.is_matched
        assert "iphone 14 128gb" in iphone.best_match.competitor_product.title.lower()
        ai_rejected_iphone = [
            c.competitor_product.title.lower()
            for c in iphone.rejected_candidates
            if c.match_details.get("ai_is_match") is False
        ]
        assert any("cover" in title for title in ai_rejected_iphone)

    def test_legit_listings_survive_the_ai_gate(
        self,
        dirty_catalog_path: Path,
        dirty_oxylabs_path: Path,
        settings,
        fake_encoder,
    ):
        """Genuine matches are confirmed (AI_VERIFIED), not collateral damage."""
        catalog = CatalogIngestor().ingest(dirty_catalog_path)
        competitors = OxyLabsIngestor().ingest(dirty_oxylabs_path)
        gate = AiQualityGateLayer(PredicateGroqClient(_looks_like_real_product))
        pipeline = MatchingPipeline(settings, skip_ai=False, ai_gate=gate, encoder=fake_encoder)

        by_sku = _by_sku(pipeline.run(catalog, competitors, show_progress=False))

        sony = by_sku["SNY-WH1000XM5"]
        assert sony.is_matched
        assert sony.best_match.match_details.get("ai_is_match") is True
