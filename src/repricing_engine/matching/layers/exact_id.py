"""Layer 1 — exact identifier matching (EAN / GTIN).

Solves the "EAN returns garbage" problem: even when an identifier matches, the
brand is cross-validated. A brand conflict demotes the confidence so the AI gate
(or a human) can review it instead of trusting the identifier blindly.
"""

from repricing_engine.matching.layers.base import BaseMatchLayer
from repricing_engine.models.enums import MatchMethod
from repricing_engine.models.product import (
    CatalogProduct,
    CompetitorProduct,
    MatchCandidate,
)
from repricing_engine.normalization.identifiers import normalize_ean, normalize_gtin
from repricing_engine.normalization.text import normalize_brand


class ExactIdLayer(BaseMatchLayer):
    """Match by EAN then GTIN, cross-validating the brand."""

    name = "exact_id"

    def __init__(
        self,
        base_confidence: float = 0.95,
        brand_conflict_confidence: float = 0.70,
    ) -> None:
        """Create the layer.

        Args:
            base_confidence: Confidence for an identifier match with no brand
                conflict.
            brand_conflict_confidence: Lowered confidence when the identifier
                matches but the brands clearly differ.
        """
        self.base_confidence = base_confidence
        self.brand_conflict_confidence = brand_conflict_confidence

    def match(
        self,
        catalog_product: CatalogProduct,
        competitor_products: list[CompetitorProduct],
    ) -> list[MatchCandidate]:
        """Return EAN/GTIN matches with brand cross-validation."""
        candidates: list[MatchCandidate] = []
        catalog_brand = normalize_brand(catalog_product.brand)

        for competitor in competitor_products:
            matched_on, method = self._matched_identifier(catalog_product, competitor)
            if matched_on is None:
                continue

            competitor_brand = normalize_brand(competitor.brand)
            brand_conflict = (
                catalog_brand is not None
                and competitor_brand is not None
                and catalog_brand != competitor_brand
            )
            confidence = self.brand_conflict_confidence if brand_conflict else self.base_confidence

            candidates.append(
                MatchCandidate(
                    competitor_product=competitor,
                    confidence=confidence,
                    match_method=method,
                    layer_source=self.name,
                    match_details={
                        "matched_on": matched_on,
                        "identifier": getattr(competitor, matched_on),
                        "brand_match": not brand_conflict,
                        "brand_conflict": brand_conflict,
                    },
                )
            )
        return candidates

    @staticmethod
    def _matched_identifier(
        catalog_product: CatalogProduct,
        competitor: CompetitorProduct,
    ) -> tuple[str | None, MatchMethod]:
        """Return which identifier matched (preferring EAN) and its method."""
        catalog_ean = normalize_ean(catalog_product.ean)
        competitor_ean = normalize_ean(competitor.ean)
        if catalog_ean is not None and catalog_ean == competitor_ean:
            return "ean", MatchMethod.EXACT_EAN

        catalog_gtin = normalize_gtin(catalog_product.gtin)
        competitor_gtin = normalize_gtin(competitor.gtin)
        if catalog_gtin is not None and catalog_gtin == competitor_gtin:
            return "gtin", MatchMethod.EXACT_GTIN

        return None, MatchMethod.EXACT_EAN
