"""Unit tests for the PDP verifier cascade (no network — MockTransport)."""

from decimal import Decimal

import httpx

from repricing_engine.models.enums import Availability, Market, MatchMethod
from repricing_engine.models.product import CatalogProduct, CompetitorProduct, MatchCandidate
from repricing_engine.pdp.fetcher import PageFetcher
from repricing_engine.pdp.identifier_finder import IdentifierFinder
from repricing_engine.pdp.models import PdpExtractionResult
from repricing_engine.pdp.verifier import PdpVerifier


def _catalog(**overrides) -> CatalogProduct:
    base = {
        "sku": "APL-IPH13-128",
        "ean": "4006381333931",
        "gtin": "4006381333931",
        "brand": "Apple",
        "title": "Apple iPhone 13 128GB Blue",
        "category": "Smartphones",
        "market": Market.IT,
    }
    base.update(overrides)
    return CatalogProduct(**base)


def _candidate(
    url: str = "https://shop.it/p", *, price="999.00", confidence=0.95
) -> MatchCandidate:
    competitor = CompetitorProduct(
        source="searxng",
        source_id="X1",
        title="iPhone 13",
        price=None if price is None else Decimal(price),
        currency="EUR",
        url=url,
        market=Market.IT,
        source_provider="searxng",
    )
    return MatchCandidate(
        competitor_product=competitor,
        confidence=confidence,
        match_method=MatchMethod.SKU_BRAND,
        layer_source="sku_brand",
    )


class _FakeExtractor:
    def __init__(self, result: PdpExtractionResult | None) -> None:
        self._result = result
        self.calls = 0

    async def extract(self, html, product, url) -> PdpExtractionResult | None:
        self.calls += 1
        return self._result


def _verifier(client, extractor=None, *, ai_threshold=0.75) -> PdpVerifier:
    return PdpVerifier(
        PageFetcher(client, rate_limit_per_domain_seconds=0.0),
        IdentifierFinder(),
        ai_extractor=extractor,
        ai_confidence_threshold=ai_threshold,
    )


class TestPdpVerifier:
    async def test_json_ld_confirms_and_extracts(self, mock_async_client, fixture_text):
        html = fixture_text("pdp_with_jsonld.html")
        extractor = _FakeExtractor(None)
        client = mock_async_client(lambda request: httpx.Response(200, text=html))
        async with client:
            verifier = _verifier(client, extractor)
            verified = await verifier.verify_candidates(_catalog(), [_candidate()], 15)

        candidate = verified[0]
        assert candidate.match_details["pdp_verified"] is True
        assert candidate.match_details["confirmation_method"] == "PDP·GTIN"
        assert candidate.match_details["extraction_method"] == "json_ld"
        assert candidate.competitor_product.price == Decimal("789.00")
        assert candidate.competitor_product.availability is Availability.IN_STOCK
        assert extractor.calls == 0  # free pass was enough — no AI

    async def test_fetch_failure_keeps_original(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(404, text="nope"))
        async with client:
            verifier = _verifier(client)
            verified = await verifier.verify_candidates(_catalog(), [_candidate()], 15)

        candidate = verified[0]
        assert candidate.match_details["pdp_verified"] is False
        assert candidate.competitor_product.price == Decimal("999.00")

    async def test_ai_fallback_when_free_pass_inconclusive(self, mock_async_client, fixture_text):
        html = fixture_text("pdp_without_jsonld.html")
        extractor = _FakeExtractor(PdpExtractionResult(price=Decimal("120.00"), currency="EUR"))
        client = mock_async_client(lambda request: httpx.Response(200, text=html))
        async with client:
            verifier = _verifier(client, extractor)
            catalog = _catalog(ean=None, gtin=None, sku="ZZZ-NOTFOUND")
            verified = await verifier.verify_candidates(catalog, [_candidate(confidence=0.5)], 15)

        candidate = verified[0]
        assert extractor.calls == 1
        assert candidate.match_details["extraction_method"] == "ai"
        assert candidate.competitor_product.price == Decimal("120.00")

    async def test_no_ai_when_confidence_above_threshold(self, mock_async_client, fixture_text):
        html = fixture_text("pdp_without_jsonld.html")
        extractor = _FakeExtractor(PdpExtractionResult(price=Decimal("120.00")))
        client = mock_async_client(lambda request: httpx.Response(200, text=html))
        async with client:
            verifier = _verifier(client, extractor)
            catalog = _catalog(ean=None, gtin=None, sku="ZZZ-NOTFOUND")
            verified = await verifier.verify_candidates(catalog, [_candidate(confidence=0.95)], 15)

        assert extractor.calls == 0
        assert verified[0].match_details["pdp_verified"] is False

    async def test_cap_defers_extra_candidates(self, mock_async_client, fixture_text):
        html = fixture_text("pdp_with_jsonld.html")
        client = mock_async_client(lambda request: httpx.Response(200, text=html))
        async with client:
            verifier = _verifier(client)
            candidates = [_candidate("https://a.it/p"), _candidate("https://b.it/p")]
            verified = await verifier.verify_candidates(_catalog(), candidates, 1)

        assert verified[0].match_details["pdp_verified"] is True
        assert "pdp_verified" not in verified[1].match_details  # deferred, untouched

    async def test_no_url_skips(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, text="x"))
        async with client:
            verifier = _verifier(client)
            verified = await verifier.verify_candidates(_catalog(), [_candidate(url="")], 15)

        assert verified[0].match_details["pdp_verified"] is False
        assert verified[0].match_details["pdp_skip_reason"] == "no_url"
