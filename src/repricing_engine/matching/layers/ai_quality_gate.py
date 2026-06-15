"""Layer 4 — AI quality gate.

Sends each surviving candidate to a Groq-hosted Llama model for a final verdict.
The Groq client is dependency-injected (stubbed in tests). If Groq is
unavailable, the gate degrades gracefully: candidates keep their prior
confidence and a warning is logged.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from tenacity import retry, stop_after_attempt, wait_exponential

from repricing_engine.models.enums import MatchMethod

if TYPE_CHECKING:
    from groq import Groq

    from repricing_engine.models.product import CatalogProduct, MatchCandidate

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a product-matching quality auditor for an e-commerce repricing "
    "engine. Decide whether a competitor product is the same sellable item as a "
    "catalog product. Respond ONLY with a JSON object: "
    '{"is_match": bool, "confidence_adjustment": float between -0.3 and 0.1, '
    '"reason": string}.'
)

_MIN_ADJUSTMENT = -0.3
_MAX_ADJUSTMENT = 0.1


class AiQualityGateLayer:
    """LLM-based verifier that confirms/rejects and re-scores candidates."""

    name = "ai_quality_gate"

    def __init__(self, client: Groq, model: str = "llama-3.3-70b-versatile") -> None:
        """Create the gate.

        Args:
            client: A Groq client (or any object exposing the same
                ``chat.completions.create`` interface).
            model: The Groq model id to query.
        """
        self.client = client
        self.model = model

    def review(
        self,
        catalog_product: CatalogProduct,
        candidates: list[MatchCandidate],
    ) -> list[MatchCandidate]:
        """Return candidates annotated with the AI verdict and adjusted scores.

        Failures (after retries) leave a candidate untouched so the pipeline can
        fall back to the previous layers' confidence.
        """
        reviewed: list[MatchCandidate] = []
        for candidate in candidates:
            reviewed.append(self._review_one(catalog_product, candidate))
        return reviewed

    def _review_one(
        self,
        catalog_product: CatalogProduct,
        candidate: MatchCandidate,
    ) -> MatchCandidate:
        """Apply the AI verdict to a single candidate."""
        prompt = self._build_prompt(catalog_product, candidate)
        try:
            verdict = self._verdict(prompt)
        except Exception as exc:  # Groq down / malformed — keep prior confidence
            logger.warning("AI quality gate skipped a candidate: %s", exc)
            details = {**candidate.match_details, "ai_gate": "skipped", "ai_error": str(exc)}
            return candidate.model_copy(update={"match_details": details})

        raw_adjustment = float(verdict["confidence_adjustment"])
        adjustment = max(_MIN_ADJUSTMENT, min(_MAX_ADJUSTMENT, raw_adjustment))
        is_match = bool(verdict["is_match"])
        new_confidence = max(0.0, min(1.0, candidate.confidence + adjustment))

        details = {
            **candidate.match_details,
            "ai_is_match": is_match,
            "ai_confidence_adjustment": round(adjustment, 4),
            "ai_reason": str(verdict.get("reason", "")),
        }
        return candidate.model_copy(
            update={
                "confidence": round(new_confidence, 4),
                "match_method": MatchMethod.AI_VERIFIED if is_match else candidate.match_method,
                "match_details": details,
            }
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=4), reraise=True)
    def _create_completion(self, prompt: str) -> str:
        """Call Groq and return the raw message content (retried on failure)."""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return response.choices[0].message.content

    def _verdict(self, prompt: str) -> dict[str, object]:
        """Get and parse a JSON verdict from the model."""
        content = self._create_completion(prompt)
        parsed = json.loads(content)
        if "is_match" not in parsed or "confidence_adjustment" not in parsed:
            msg = f"AI gate returned an unexpected payload: {parsed!r}"
            raise ValueError(msg)
        return parsed

    @staticmethod
    def _build_prompt(catalog_product: CatalogProduct, candidate: MatchCandidate) -> str:
        """Build the user prompt comparing catalog and competitor products."""
        competitor = candidate.competitor_product
        return (
            "Given this catalog product:\n"
            f"  - Brand: {catalog_product.brand}, Title: {catalog_product.title}, "
            f"SKU: {catalog_product.sku}, EAN: {catalog_product.ean}, "
            f"Category: {catalog_product.category}\n\n"
            "Is this a correct match?\n"
            f"  - Brand: {competitor.brand}, Title: {competitor.title}, "
            f"SKU: {competitor.sku}, EAN: {competitor.ean}, Price: {competitor.price}\n"
            f"  - Match method: {candidate.match_method}, "
            f"Current confidence: {candidate.confidence}\n\n"
            'Respond with JSON: {"is_match": bool, "confidence_adjustment": '
            'float (-0.3 to +0.1), "reason": "..."}'
        )
