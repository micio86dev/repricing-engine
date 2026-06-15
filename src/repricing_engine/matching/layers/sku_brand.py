"""Layer 2 — fuzzy SKU matching boosted by brand agreement."""

from rapidfuzz import fuzz

from repricing_engine.matching.layers.base import BaseMatchLayer
from repricing_engine.models.enums import MatchMethod
from repricing_engine.models.product import (
    CatalogProduct,
    CompetitorProduct,
    MatchCandidate,
)
from repricing_engine.normalization.identifiers import normalize_sku
from repricing_engine.normalization.text import normalize_brand

# Brand boost factors applied to the fuzzy SKU score.
_BRAND_MATCH_BOOST = 1.0
_BRAND_MISSING_BOOST = 0.7
_BRAND_CONFLICT_BOOST = 0.3


class SkuBrandLayer(BaseMatchLayer):
    """Match by fuzzy SKU similarity, weighted by brand agreement.

    ``confidence = fuzzy_sku_score * brand_boost`` where the boost is ``1.0`` for
    a brand match, ``0.7`` when a brand is missing, and ``0.3`` on a conflict.
    """

    name = "sku_brand"

    def match(
        self,
        catalog_product: CatalogProduct,
        competitor_products: list[CompetitorProduct],
    ) -> list[MatchCandidate]:
        """Return fuzzy SKU candidates with brand-weighted confidence."""
        catalog_sku = normalize_sku(catalog_product.sku)
        if not catalog_sku:
            return []

        catalog_brand = normalize_brand(catalog_product.brand)
        candidates: list[MatchCandidate] = []

        for competitor in competitor_products:
            competitor_sku = normalize_sku(competitor.sku)
            if not competitor_sku:
                continue

            fuzzy_score = fuzz.token_sort_ratio(catalog_sku, competitor_sku) / 100.0
            brand_boost, brand_state = self._brand_boost(
                catalog_brand, normalize_brand(competitor.brand)
            )
            confidence = round(fuzzy_score * brand_boost, 4)

            candidates.append(
                MatchCandidate(
                    competitor_product=competitor,
                    confidence=confidence,
                    match_method=MatchMethod.SKU_BRAND,
                    layer_source=self.name,
                    match_details={
                        "catalog_sku": catalog_sku,
                        "competitor_sku": competitor_sku,
                        "fuzzy_score": round(fuzzy_score, 4),
                        "brand_state": brand_state,
                        "brand_boost": brand_boost,
                    },
                )
            )
        return candidates

    @staticmethod
    def _brand_boost(
        catalog_brand: str | None,
        competitor_brand: str | None,
    ) -> tuple[float, str]:
        """Return the brand boost factor and a label describing the brand state."""
        if competitor_brand is None or catalog_brand is None:
            return _BRAND_MISSING_BOOST, "missing"
        if catalog_brand == competitor_brand:
            return _BRAND_MATCH_BOOST, "match"
        return _BRAND_CONFLICT_BOOST, "conflict"
