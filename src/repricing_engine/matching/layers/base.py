"""Abstract base class for matching layers."""

from abc import ABC, abstractmethod

from repricing_engine.models.product import (
    CatalogProduct,
    CompetitorProduct,
    MatchCandidate,
)


class BaseMatchLayer(ABC):
    """A single stage of the matching cascade.

    Implementations return zero or more :class:`MatchCandidate`s, each carrying a
    confidence score and an explanation in ``match_details``.
    """

    #: Human-readable layer name, surfaced in ``layer_source``.
    name: str = "base"

    @abstractmethod
    def match(
        self,
        catalog_product: CatalogProduct,
        competitor_products: list[CompetitorProduct],
    ) -> list[MatchCandidate]:
        """Return candidate matches for ``catalog_product``."""
