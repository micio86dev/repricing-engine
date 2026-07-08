"""Unit tests for the SourceFetcher orchestrator."""

from repricing_engine.exceptions import SourceFetchError
from repricing_engine.models.enums import Market
from repricing_engine.models.product import CatalogProduct
from repricing_engine.sources.base import BaseSourceProvider
from repricing_engine.sources.models import RawSearchResult
from repricing_engine.sources.orchestrator import SourceFetcher


def _catalog() -> CatalogProduct:
    return CatalogProduct(
        sku="SKU1", brand="Acme", title="Widget", category="Misc", market=Market.IT
    )


class _FakeProvider(BaseSourceProvider):
    def __init__(self, name, results, *, available=True, cost_tier=0, error=None):
        self.name = name
        self.cost_tier = cost_tier
        self._results = results
        self._available = available
        self._error = error
        self.searched = False

    def is_available(self) -> bool:
        return self._available

    async def search(self, product, market):
        self.searched = True
        if self._error is not None:
            raise self._error
        return self._results


def _raw(provider: str, url: str) -> RawSearchResult:
    return RawSearchResult(source_provider=provider, title="Item", url=url)


class TestSourceFetcher:
    async def test_merges_and_maps_results(self):
        fetcher = SourceFetcher(
            [
                _FakeProvider("a", [_raw("a", "https://alpha.it/p")]),
                _FakeProvider("b", [_raw("b", "https://beta.it/p")]),
            ]
        )
        competitors = await fetcher.fetch_competitors(_catalog(), Market.IT)
        domains = {c.seller for c in competitors}
        assert domains == {"alpha.it", "beta.it"}

    async def test_one_provider_failure_does_not_sink_batch(self):
        good = _FakeProvider("good", [_raw("good", "https://alpha.it/p")])
        bad = _FakeProvider("bad", [], error=SourceFetchError("boom"))
        fetcher = SourceFetcher([good, bad])
        competitors = await fetcher.fetch_competitors(_catalog(), Market.IT)
        assert len(competitors) == 1
        assert competitors[0].seller == "alpha.it"

    async def test_unavailable_provider_skipped(self):
        off = _FakeProvider("off", [_raw("off", "https://x.it/p")], available=False)
        fetcher = SourceFetcher([off])
        competitors = await fetcher.fetch_competitors(_catalog(), Market.IT)
        assert competitors == []
        assert off.searched is False

    async def test_no_providers_returns_empty(self):
        assert await SourceFetcher([]).fetch_competitors(_catalog(), Market.IT) == []

    def test_providers_sorted_by_cost_tier(self):
        cheap = _FakeProvider("cheap", [], cost_tier=0)
        pricey = _FakeProvider("pricey", [], cost_tier=2)
        fetcher = SourceFetcher([pricey, cheap])
        assert [p.name for p in fetcher.providers] == ["cheap", "pricey"]
