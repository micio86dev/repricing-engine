"""AiExtractor — last-resort structured extraction of offer data via Groq.

Only invoked when the free identifier/JSON-LD pass can't confirm a page or read a
price (Groq tokens cost money). The page HTML is stripped to its meaningful text
and truncated before prompting; the model returns a small JSON object that is
validated into a :class:`PdpExtractionResult`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from repricing_engine.normalization.availability import normalize_availability
from repricing_engine.normalization.price import parse_price_loose
from repricing_engine.pdp.models import PdpExtractionResult

if TYPE_CHECKING:
    from groq import Groq

    from repricing_engine.config import Settings
    from repricing_engine.models.product import CatalogProduct

logger = logging.getLogger(__name__)

# Tags that never carry offer data — dropped before extracting text.
_NOISE_TAGS: tuple[str, ...] = ("script", "style", "nav", "header", "footer", "aside", "form")
_MAX_TEXT_CHARS = 4000

_SYSTEM_PROMPT = (
    "You extract the offer details for a single product from an e-commerce page. "
    "Respond ONLY with a JSON object: "
    '{"price": number|null, "currency": string|null, "shipping_cost": number|null, '
    '"availability": string|null, "seller": string|null, '
    '"confidence": number between 0 and 1}. '
    "Use null when a field is not present. Prices must be plain numbers."
)


class AiExtractor:
    """Groq-backed extractor for offer price / shipping / availability."""

    def __init__(self, client: Groq, model: str = "llama-3.3-70b-versatile") -> None:
        """Create the extractor.

        Args:
            client: A Groq client (or any object exposing the same
                ``chat.completions.create`` interface).
            model: The Groq model id to query.
        """
        self.client = client
        self.model = model

    async def extract(
        self,
        html: str,
        catalog_product: CatalogProduct,
        url: str,
    ) -> PdpExtractionResult | None:
        """Extract offer data from a page, or ``None`` on failure.

        The synchronous Groq call is offloaded with :func:`asyncio.to_thread` so
        the event loop is never blocked.
        """
        prompt = self._build_prompt(html, catalog_product, url)
        try:
            content = await asyncio.to_thread(self._create_completion, prompt)
            payload = json.loads(content)
        except Exception as exc:
            logger.warning("AI extraction failed for %s: %s", url, exc)
            return None
        return self._to_result(payload)

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

    def _build_prompt(self, html: str, catalog_product: CatalogProduct, url: str) -> str:
        """Build the user prompt from cleaned page text + the catalog product."""
        text = self._clean_html(html)
        return (
            f"Catalog product: brand={catalog_product.brand!r}, "
            f"title={catalog_product.title!r}, sku={catalog_product.sku!r}, "
            f"ean={catalog_product.ean!r}.\n"
            f"Page URL: {url}\n"
            f"Page text (truncated):\n{text}\n\n"
            "Extract the offer for this exact product as the specified JSON."
        )

    @staticmethod
    def _clean_html(html: str) -> str:
        """Strip noise tags and collapse to truncated visible text."""
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(_NOISE_TAGS):
            tag.decompose()
        main = soup.find("main") or soup.body or soup
        text = main.get_text(" ", strip=True)
        return text[:_MAX_TEXT_CHARS]

    def _to_result(self, payload: dict[str, object]) -> PdpExtractionResult:
        """Validate/normalize the model payload into a :class:`PdpExtractionResult`."""
        currency = payload.get("currency")
        confidence = payload.get("confidence", 0.0)
        return PdpExtractionResult(
            price=parse_price_loose(payload.get("price")),  # type: ignore[arg-type]
            currency=str(currency).upper() if currency else None,
            shipping_cost=parse_price_loose(payload.get("shipping_cost")),  # type: ignore[arg-type]
            availability=normalize_availability(
                str(payload["availability"]) if payload.get("availability") else None
            ),
            seller=str(payload["seller"]) if payload.get("seller") else None,
            confidence=max(0.0, min(1.0, float(confidence) if confidence is not None else 0.0)),
        )


def build_ai_extractor(settings: Settings) -> AiExtractor | None:
    """Build a Groq-backed extractor from settings, or ``None`` if unavailable.

    Mirrors :meth:`MatchingPipeline._build_ai_gate` so the two share one Groq
    construction pattern.
    """
    if not settings.groq_api_key:
        logger.info("No GROQ_API_KEY set — PDP AI extraction disabled.")
        return None
    try:
        from groq import Groq

        client = Groq(api_key=settings.groq_api_key)
    except Exception as exc:
        logger.warning("Could not initialize Groq client — PDP AI extraction disabled (%s)", exc)
        return None
    return AiExtractor(client, model=settings.groq_model)
