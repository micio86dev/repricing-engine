"""Unit tests for source-result deduplication."""

from decimal import Decimal

from repricing_engine.sources.deduplicator import deduplicate, normalize_url
from repricing_engine.sources.models import RawSearchResult


def _result(url: str, *, price: str | None = None, shipping: str | None = None, snippet=None):
    return RawSearchResult(
        source_provider="searxng",
        title="Item",
        url=url,
        snippet=snippet,
        price=None if price is None else Decimal(price),
        shipping_cost=None if shipping is None else Decimal(shipping),
    )


class TestNormalizeUrl:
    def test_strips_tracking_params_and_fragment(self):
        url = "https://WWW.Shop.it/p/123/?utm_source=g&ref=abc&color=blue#frag"
        assert normalize_url(url) == "https://shop.it/p/123?color=blue"

    def test_trailing_slash_and_scheme(self):
        assert normalize_url("https://shop.it/p/") == "https://shop.it/p"


class TestDeduplicate:
    def test_same_url_keeps_richer(self):
        bare = _result("https://shop.it/p")
        rich = _result("https://shop.it/p?utm_source=x", price="10.00", shipping="0.00")
        out = deduplicate([bare, rich])
        assert len(out) == 1
        assert out[0].price == Decimal("10.00")

    def test_same_domain_keeps_lowest_price(self):
        a = _result("https://shop.it/a", price="20.00")
        b = _result("https://shop.it/b", price="15.00")
        out = deduplicate([a, b])
        assert len(out) == 1
        assert out[0].price == Decimal("15.00")

    def test_different_domains_all_kept_in_order(self):
        a = _result("https://alpha.it/x", price="20.00")
        b = _result("https://beta.it/y", price="15.00")
        out = deduplicate([a, b])
        assert [r.url for r in out] == ["https://alpha.it/x", "https://beta.it/y"]

    def test_domain_without_price_keeps_richest(self):
        thin = _result("https://shop.it/a")
        rich = _result("https://shop.it/b", snippet="has a snippet")
        out = deduplicate([thin, rich])
        assert len(out) == 1
        assert out[0].snippet == "has a snippet"
