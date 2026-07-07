"""PdpVerifier — confirm matched candidates against their real product pages.

Per candidate, cheapest signal first:

1. **Fetch** the page (httpx, optional Playwright).
2. **Free scan** for the catalog EAN/GTIN/SKU and any Product JSON-LD. A hit sets
   a real ``confirmation_method`` (``PDP·GTIN`` / ``PDP·EAN`` / ``PDP·SKU`` /
   ``json_ld``) and, via JSON-LD, often the price/shipping/stock.
3. **AI extraction** (last resort) only when the free pass found neither an
   identifier nor a price *and* the candidate is still below the confidence bar.

The verified offer is written back through ``CompetitorProduct.model_copy`` and
the candidate's ``match_details`` is annotated; failures degrade to the original
data with ``pdp_verified=False`` (zero data loss).
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from typing import TYPE_CHECKING, Any

from repricing_engine.matching.scoring import select_best
from repricing_engine.models.enums import Availability
from repricing_engine.normalization.availability import normalize_availability
from repricing_engine.normalization.identifiers import normalize_ean, normalize_gtin
from repricing_engine.normalization.price import extract_shipping_from_text, parse_price_loose
from repricing_engine.pdp.fetcher import PageFetcher
from repricing_engine.pdp.identifier_finder import IdentifierFinder
from repricing_engine.pdp.models import PdpExtractionResult

if TYPE_CHECKING:
    import httpx

    from repricing_engine.config import Settings
    from repricing_engine.models.match_result import MatchResult
    from repricing_engine.models.product import CatalogProduct, CompetitorProduct, MatchCandidate
    from repricing_engine.pdp.extractor import AiExtractor
    from repricing_engine.pdp.models import FetchResult, IdentifierFindings

logger = logging.getLogger(__name__)

# How many SKU-confirmed pages must agree on a GTIN before we adopt it as the EAN.
_GTIN_CONSENSUS_MIN = 2


def _canonical_barcode(value: object) -> str | None:
    """Return a checksum-valid EAN/GTIN in canonical digit form, or ``None``."""
    if value is None:
        return None
    text = str(value)
    return normalize_gtin(text) or normalize_ean(text)


class PdpVerifier:
    """Verify match candidates by visiting their product pages."""

    def __init__(
        self,
        fetcher: PageFetcher,
        identifier_finder: IdentifierFinder,
        *,
        ai_extractor: AiExtractor | None = None,
        ai_extraction_enabled: bool = True,
        ai_confidence_threshold: float = 0.75,
    ) -> None:
        """Create the verifier.

        Args:
            fetcher: The page fetcher.
            identifier_finder: The free identifier/JSON-LD scanner.
            ai_extractor: Optional Groq extractor (last-resort price extraction).
            ai_extraction_enabled: Master switch for the AI extraction step.
            ai_confidence_threshold: AI runs only when a candidate's confidence
                is below this and the free pass found neither identifier nor price.
        """
        self._fetcher = fetcher
        self._finder = identifier_finder
        self._ai_extractor = ai_extractor
        self._ai_enabled = ai_extraction_enabled
        self._ai_threshold = ai_confidence_threshold

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        client: httpx.AsyncClient,
        *,
        ai_extractor: AiExtractor | None = None,
    ) -> PdpVerifier:
        """Build a verifier (fetcher + finder) from settings."""
        fetcher = PageFetcher(
            client,
            timeout_seconds=settings.pdp_fetch_timeout_seconds,
            max_concurrent=settings.pdp_max_concurrent_fetches,
            rate_limit_per_domain_seconds=settings.pdp_rate_limit_per_domain_seconds,
            playwright_enabled=settings.pdp_playwright_enabled,
        )
        return cls(
            fetcher,
            IdentifierFinder(),
            ai_extractor=ai_extractor,
            ai_extraction_enabled=settings.pdp_ai_extraction_enabled,
            ai_confidence_threshold=settings.pdp_ai_confidence_threshold,
        )

    async def verify_result(
        self,
        catalog_product: CatalogProduct,
        result: MatchResult,
        max_pdp_per_product: int,
    ) -> MatchResult:
        """Verify a product's candidates and return an updated :class:`MatchResult`."""
        verified = await self.verify_candidates(
            catalog_product, result.all_candidates, max_pdp_per_product
        )
        recovered_catalog, verified = self._recover_barcode(catalog_product, verified)
        verified.sort(key=lambda c: c.confidence, reverse=True)
        return result.model_copy(
            update={
                "catalog_product": recovered_catalog,
                "all_candidates": verified,
                "best_match": select_best(verified),
            }
        )

    @staticmethod
    def _recover_barcode(
        catalog_product: CatalogProduct,
        candidates: list[MatchCandidate],
    ) -> tuple[CatalogProduct, list[MatchCandidate]]:
        """Recover the product's real EAN/GTIN from a cross-page consensus.

        Only runs when the catalog has no valid barcode of its own. Adopts a GTIN
        seen (identically) on **two or more** SKU-confirmed pages, writes it back to
        the catalog product (fills ``product_ean``), and upgrades those offers'
        confirmation to ``PDP·GTIN``. Requiring agreement across pages guards against
        a single mislabeled page.
        """
        if normalize_ean(catalog_product.ean) or normalize_gtin(catalog_product.gtin):
            return catalog_product, candidates

        seen = [c.match_details.get("page_gtin") for c in candidates]
        counts = Counter(g for g in seen if g)
        if not counts:
            return catalog_product, candidates
        gtin, agree = counts.most_common(1)[0]
        if agree < _GTIN_CONSENSUS_MIN:
            return catalog_product, candidates

        recovered = catalog_product.model_copy(update={"ean": gtin, "gtin": gtin})
        upgraded: list[MatchCandidate] = []
        for candidate in candidates:
            page_gtin = candidate.match_details.get("page_gtin")
            if page_gtin == gtin:
                # Page barcode agrees with the consensus -> strongest confirmation.
                details = {
                    **candidate.match_details,
                    "confirmation_method": "PDP·GTIN",
                    "gtin_recovered": True,
                }
                upgraded.append(candidate.model_copy(update={"match_details": details}))
            elif page_gtin is not None:
                # SKU matched but the page shows a *different* barcode: likely a
                # variant/lookalike. Flag it so strict mode can exclude it.
                details = {**candidate.match_details, "gtin_conflict": page_gtin}
                upgraded.append(candidate.model_copy(update={"match_details": details}))
            else:
                upgraded.append(candidate)
        logger.info(
            "Recovered EAN %s for %s from %d agreeing product page(s).",
            gtin,
            catalog_product.sku,
            agree,
        )
        return recovered, upgraded

    async def verify_candidates(
        self,
        catalog_product: CatalogProduct,
        candidates: list[MatchCandidate],
        max_pdp_per_product: int,
    ) -> list[MatchCandidate]:
        """Verify up to ``max_pdp_per_product`` candidates concurrently.

        Candidates beyond the cap are returned unchanged (logged), so the cap can
        never silently drop offers. A ``max_pdp_per_product <= 0`` means *no cap* —
        every candidate is verified.
        """
        if max_pdp_per_product <= 0:
            to_verify, deferred = candidates, []
        else:
            to_verify = candidates[:max_pdp_per_product]
            deferred = candidates[max_pdp_per_product:]
        if deferred:
            logger.info(
                "PDP cap (%d) reached for %s; %d candidate(s) left unverified.",
                max_pdp_per_product,
                catalog_product.sku,
                len(deferred),
            )
        verified = await asyncio.gather(
            *(self._verify_one(catalog_product, candidate) for candidate in to_verify)
        )
        return [*verified, *deferred]

    async def _verify_one(
        self,
        catalog_product: CatalogProduct,
        candidate: MatchCandidate,
    ) -> MatchCandidate:
        """Run the verification cascade for one candidate (never raises)."""
        url = candidate.competitor_product.url
        if not url:
            return self._annotate(candidate, verified=False, reason="no_url")
        try:
            fetch = await self._fetcher.fetch(url, market=catalog_product.market)
            if not fetch.ok:
                return self._annotate(candidate, verified=False, reason="fetch_failed")
            return await self._apply_findings(catalog_product, candidate, fetch)
        except Exception:
            logger.warning("PDP verification errored for %s; keeping original.", url, exc_info=True)
            return self._annotate(candidate, verified=False, reason="error")

    async def _apply_findings(
        self,
        catalog_product: CatalogProduct,
        candidate: MatchCandidate,
        fetch: FetchResult,
    ) -> MatchCandidate:
        """Classify findings, extract offer data, and write the verified offer back."""
        findings = self._finder.find_identifiers(fetch.html, catalog_product)
        confirmation = self._confirmation_method(findings)
        extraction = self._extraction_from_json_ld(findings)
        extraction_method = "json_ld" if extraction.price is not None else None

        # Free price fallback: many IT shops expose the price only in OpenGraph/microdata.
        if extraction.price is None and findings.meta_price is not None:
            meta_price = parse_price_loose(findings.meta_price)
            if meta_price is not None:
                extraction = extraction.model_copy(
                    update={
                        "price": meta_price,
                        "currency": extraction.currency or (findings.meta_currency or None),
                    }
                )
                extraction_method = "meta"
        if findings.any_found and extraction_method is None:
            extraction_method = "regex"

        # Shipping cascade (all free, cheapest signal first): JSON-LD → <meta> →
        # page text. Structured shipping is rare, so fall back to meta tags, then a
        # precise text scan reading "Spedizione gratis" (→ 0) or "Spedizione 4,99€".
        if extraction.shipping_cost is None and findings.meta_shipping is not None:
            meta_shipping = parse_price_loose(findings.meta_shipping)
            if meta_shipping is not None:
                extraction = extraction.model_copy(update={"shipping_cost": meta_shipping})
        if extraction.shipping_cost is None:
            text_shipping = extract_shipping_from_text(
                fetch.html, candidate.competitor_product.market
            )
            if text_shipping is not None:
                extraction = extraction.model_copy(update={"shipping_cost": text_shipping})

        if self._needs_ai(findings, extraction, candidate):
            ai_result = await self._ai_extractor.extract(fetch.html, catalog_product, fetch.url)
            if ai_result is not None and ai_result.price is not None:
                extraction = ai_result
                extraction_method = "ai"
                confirmation = confirmation or "ai"

        verified = confirmation is not None
        updated_offer = self._apply_extraction(candidate.competitor_product, extraction)
        details: dict[str, Any] = {
            **candidate.match_details,
            "pdp_verified": verified,
            "confirmation_method": confirmation,
            "extraction_method": extraction_method,
            "source_provider": candidate.competitor_product.source_provider,
            "fetch_method": fetch.fetch_method,
            "found_identifiers": findings.found_identifiers,
        }
        # When the SKU is confirmed on the page, its JSON-LD GTIN is this product's
        # barcode — record it (canonicalized) so a cross-page consensus can recover
        # the real EAN even though the catalog's EAN column is corrupt.
        if findings.sku_found:
            page_gtin = _canonical_barcode(findings.json_ld_data.get("gtin"))
            if page_gtin is not None:
                details["page_gtin"] = page_gtin
        return candidate.model_copy(
            update={"competitor_product": updated_offer, "match_details": details}
        )

    @staticmethod
    def _confirmation_method(findings: IdentifierFindings) -> str | None:
        """Pick the strongest real confirmation label for the findings."""
        if findings.gtin_found:
            return "PDP·GTIN"
        if findings.ean_found:
            return "PDP·EAN"
        if findings.sku_found:
            return "PDP·SKU"
        if findings.has_json_ld:
            return "json_ld"
        return None

    def _needs_ai(
        self,
        findings: IdentifierFindings,
        extraction: PdpExtractionResult,
        candidate: MatchCandidate,
    ) -> bool:
        """True only when the free pass is inconclusive and AI is warranted."""
        if not (self._ai_enabled and self._ai_extractor is not None):
            return False
        if findings.any_found or extraction.price is not None:
            return False
        return candidate.confidence < self._ai_threshold

    def _extraction_from_json_ld(self, findings: IdentifierFindings) -> PdpExtractionResult:
        """Build an extraction result from parsed JSON-LD (free)."""
        data = findings.json_ld_data
        if not data:
            return PdpExtractionResult()
        return PdpExtractionResult(
            price=parse_price_loose(data.get("price")),
            currency=str(data["currency"]).upper() if data.get("currency") else None,
            shipping_cost=parse_price_loose(data.get("shipping")),
            availability=normalize_availability(data.get("availability")),
            seller=str(data["seller"]) if data.get("seller") else None,
            confidence=0.9,
        )

    @staticmethod
    def _apply_extraction(
        competitor: CompetitorProduct,
        extraction: PdpExtractionResult,
    ) -> CompetitorProduct:
        """Merge extracted offer data into the competitor offer (never wipes data).

        A price is only adopted when it is in the offer's own currency: a foreign
        shop that lists in HUF/SEK/PLN/... is a real page but not a currency-comparable
        competitor, so we keep the offer (URL, confirmation) without mislabeling that
        number as the market currency.
        """
        availability = (
            extraction.availability
            if extraction.availability is not Availability.UNKNOWN
            else competitor.availability
        )
        price, currency = competitor.price, competitor.currency
        if extraction.price is not None:
            extracted_currency = (extraction.currency or competitor.currency).upper()
            if extracted_currency == competitor.currency.upper():
                price, currency = extraction.price, extracted_currency
            else:
                logger.debug(
                    "Skipping %s price for %s: foreign currency, not comparable.",
                    extracted_currency,
                    competitor.url,
                )
        return competitor.model_copy(
            update={
                "price": price,
                "currency": currency,
                "shipping_cost": (
                    extraction.shipping_cost
                    if extraction.shipping_cost is not None
                    else competitor.shipping_cost
                ),
                "availability": availability,
                "seller": extraction.seller or competitor.seller,
            }
        )

    @staticmethod
    def _annotate(candidate: MatchCandidate, *, verified: bool, reason: str) -> MatchCandidate:
        """Annotate a candidate that wasn't (or couldn't be) page-verified."""
        details = {
            **candidate.match_details,
            "pdp_verified": verified,
            "pdp_skip_reason": reason,
        }
        return candidate.model_copy(update={"match_details": details})
