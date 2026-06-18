"""Unit tests for the EnhancedPipeline orchestrator (injected fakes — no network)."""

from decimal import Decimal

import httpx

from repricing_engine.matching.pipeline import MatchingPipeline
from repricing_engine.models.enums import Market
from repricing_engine.models.product import CatalogProduct, CompetitorProduct
from repricing_engine.orchestration.enhanced_pipeline import EnhancedPipeline
from tests.conftest import FakeEncoder


def _catalog(sku: str = "APL-IPH13-128") -> CatalogProduct:
    return CatalogProduct(
        sku=sku,
        ean="4006381333931",
        brand="Apple",
        title="Apple iPhone 13 128GB Blue",
        category="Smartphones",
        market=Market.IT,
    )


def _matching_competitor() -> CompetitorProduct:
    return CompetitorProduct(
        source="searxng",
        source_id="C1",
        title="Apple iPhone 13 128 GB Blue",
        price=Decimal("789.00"),
        currency="EUR",
        url="https://shop.it/p",
        market=Market.IT,
        ean="4006381333931",
        source_provider="searxng",
    )


def _pipeline(settings) -> MatchingPipeline:
    return MatchingPipeline(settings, skip_ai=True, min_confidence=0.6, encoder=FakeEncoder())


class _FakeFetcher:
    def __init__(self, by_sku) -> None:
        self.by_sku = by_sku
        self.calls: list[str] = []

    async def fetch_competitors(self, product, market):
        self.calls.append(product.sku)
        return self.by_sku.get(product.sku, [])


class _FakeVerifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def verify_result(self, product, result, max_pdp_per_product):
        self.calls.append((product.sku, max_pdp_per_product))
        if not result.all_candidates:
            return result
        first, *rest = result.all_candidates
        marked = first.model_copy(
            update={"match_details": {**first.match_details, "pdp_verified": True}}
        )
        return result.model_copy(update={"all_candidates": [marked, *rest]})


class TestEnhancedPipeline:
    async def test_fetch_adds_competitors(self, settings):
        fetcher = _FakeFetcher({"APL-IPH13-128": [_matching_competitor()]})
        enhanced = EnhancedPipeline(_pipeline(settings), source_fetcher=fetcher)
        results = await enhanced.run(
            [_catalog()], csv_pool=[], market=Market.IT, fetch=True, show_progress=False
        )
        assert fetcher.calls == ["APL-IPH13-128"]
        assert results[0].is_matched

    async def test_verify_pdp_invokes_verifier(self, settings):
        verifier = _FakeVerifier()
        enhanced = EnhancedPipeline(_pipeline(settings), pdp_verifier=verifier)
        results = await enhanced.run(
            [_catalog()],
            csv_pool=[_matching_competitor()],
            market=Market.IT,
            verify_pdp=True,
            max_pdp_per_product=7,
            show_progress=False,
        )
        assert verifier.calls == [("APL-IPH13-128", 7)]
        assert results[0].all_candidates[0].match_details["pdp_verified"] is True

    async def test_plain_matching_without_flags(self, settings):
        enhanced = EnhancedPipeline(_pipeline(settings))
        results = await enhanced.run(
            [_catalog()], csv_pool=[_matching_competitor()], market=Market.IT, show_progress=False
        )
        assert results[0].is_matched

    async def test_results_in_input_order(self, settings):
        enhanced = EnhancedPipeline(_pipeline(settings))
        catalog = [_catalog("SKU-A"), _catalog("SKU-B"), _catalog("SKU-C")]
        results = await enhanced.run(catalog, csv_pool=[], market=Market.IT, show_progress=False)
        assert [r.catalog_product.sku for r in results] == ["SKU-A", "SKU-B", "SKU-C"]

    async def test_build_wires_components(self, settings, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, text=""))
        async with client:
            enhanced = EnhancedPipeline.build(
                settings,
                client,
                matching_pipeline=_pipeline(settings),
                fetch=True,
                verify_pdp=False,
            )
            assert enhanced._source_fetcher is not None
            assert enhanced._pdp_verifier is None
