"""The result of matching a single catalog product against competitors."""

from pydantic import BaseModel, Field

from repricing_engine.models.product import CatalogProduct, MatchCandidate


class MatchResult(BaseModel):
    """Outcome of running the matching pipeline for one catalog product.

    Keeps *all* candidates (and the rejected ones) so the decision is auditable.
    """

    catalog_product: CatalogProduct
    best_match: MatchCandidate | None = None
    all_candidates: list[MatchCandidate] = Field(default_factory=list)
    rejected_candidates: list[MatchCandidate] = Field(default_factory=list)
    processing_time_ms: float = 0.0

    @property
    def is_matched(self) -> bool:
        """True when a best match was selected."""
        return self.best_match is not None
