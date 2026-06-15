"""Layer 3 — semantic similarity via sentence-transformer embeddings.

The encoder is dependency-injected so unit tests can stub it; in production the
``all-MiniLM-L6-v2`` model is lazily loaded on first use. Embeddings are cached
by text to avoid recomputation across comparisons.
"""

import logging
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from repricing_engine.matching.layers.base import BaseMatchLayer
from repricing_engine.models.enums import MatchMethod
from repricing_engine.models.product import (
    CatalogProduct,
    CompetitorProduct,
    MatchCandidate,
)
from repricing_engine.normalization.text import normalize_brand, normalize_title

logger = logging.getLogger(__name__)


class TextEncoder(Protocol):
    """Structural type for any object exposing a sentence encoder."""

    def encode(self, sentences: list[str]) -> NDArray[np.float64]:
        """Encode a batch of strings into a 2-D array of embeddings."""
        ...


class SemanticLayer(BaseMatchLayer):
    """Match by cosine similarity of ``brand + title + category`` embeddings."""

    name = "semantic"

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        encoder: TextEncoder | None = None,
    ) -> None:
        """Create the layer.

        Args:
            model_name: Sentence-transformer model to load when no encoder is
                injected.
            encoder: Pre-built encoder (injected in tests). If ``None`` the model
                is loaded lazily on first use.
        """
        self.model_name = model_name
        self._encoder = encoder
        self._encoder_failed = False
        self._cache: dict[str, NDArray[np.float64]] = {}

    def match(
        self,
        catalog_product: CatalogProduct,
        competitor_products: list[CompetitorProduct],
    ) -> list[MatchCandidate]:
        """Return semantic candidates scored by cosine similarity."""
        if not competitor_products:
            return []

        encoder = self._get_encoder()
        if encoder is None:
            return []

        catalog_text = self._product_text(
            catalog_product.brand, catalog_product.title, catalog_product.category
        )
        competitor_texts = [self._product_text(c.brand, c.title, "") for c in competitor_products]

        self._ensure_embeddings(encoder, [catalog_text, *competitor_texts])
        catalog_vec = self._cache[catalog_text]

        candidates: list[MatchCandidate] = []
        for competitor, text in zip(competitor_products, competitor_texts, strict=True):
            similarity = self._cosine(catalog_vec, self._cache[text])
            confidence = round(max(0.0, min(1.0, similarity)), 4)
            candidates.append(
                MatchCandidate(
                    competitor_product=competitor,
                    confidence=confidence,
                    match_method=MatchMethod.SEMANTIC,
                    layer_source=self.name,
                    match_details={
                        "cosine_similarity": round(similarity, 4),
                        "catalog_text": catalog_text,
                        "competitor_text": text,
                    },
                )
            )
        return candidates

    def _get_encoder(self) -> TextEncoder | None:
        """Return the encoder, lazily loading the model on first use."""
        if self._encoder is not None or self._encoder_failed:
            return self._encoder
        try:
            from sentence_transformers import SentenceTransformer

            self._encoder = SentenceTransformer(self.model_name)
        except Exception as exc:  # offline / missing model — degrade gracefully
            logger.warning("Semantic layer disabled: could not load %s (%s)", self.model_name, exc)
            self._encoder_failed = True
        return self._encoder

    def _ensure_embeddings(self, encoder: TextEncoder, texts: list[str]) -> None:
        """Encode and cache any texts not already in the cache."""
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if not missing:
            return
        vectors = np.asarray(encoder.encode(missing), dtype=np.float64)
        for text, vector in zip(missing, vectors, strict=True):
            self._cache[text] = vector

    @staticmethod
    def _product_text(brand: str | None, title: str, category: str) -> str:
        """Build the normalized text used for embedding a product."""
        parts = [
            normalize_brand(brand) or "",
            normalize_title(title),
            normalize_title(category),
        ]
        return " ".join(part for part in parts if part).strip()

    @staticmethod
    def _cosine(a: NDArray[np.float64], b: NDArray[np.float64]) -> float:
        """Cosine similarity between two vectors (0 if either is a zero vector)."""
        norm = float(np.linalg.norm(a) * np.linalg.norm(b))
        if norm == 0.0:
            return 0.0
        return float(np.dot(a, b) / norm)
