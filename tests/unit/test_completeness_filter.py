"""Unit tests for the price+shipping completeness filter."""

from decimal import Decimal

from repricing_engine.models.enums import Availability, Market, MatchMethod
from repricing_engine.models.match_result import MatchResult
from repricing_engine.models.product import (
    CatalogProduct,
    CompetitorProduct,
    MatchCandidate,
)
from repricing_engine.output.completeness_filter import keep_complete_offers


def _candidate(
    seller: str,
    price: str | None,
    shipping: str | None,
    confidence: float = 0.90,
) -> MatchCandidate:
    competitor = CompetitorProduct(
        source="organica",
        source_id=seller,
        title=f"Comp {seller}",
        price=None if price is None else Decimal(price),
        currency="EUR",
        url=f"https://www.{seller}.it/p",
        market=Market.IT,
        brand="Acme",
        shipping_cost=None if shipping is None else Decimal(shipping),
        availability=Availability.IN_STOCK,
        seller=seller,
    )
    return MatchCandidate(
        competitor_product=competitor,
        confidence=confidence,
        match_method=MatchMethod.SKU_BRAND,
        layer_source="sku_brand",
    )


def _result(best: MatchCandidate | None, candidates: list[MatchCandidate]) -> MatchResult:
    catalog = CatalogProduct(
        sku="SKU1", brand="Acme", title="Widget", category="Misc", market=Market.IT
    )
    return MatchResult(catalog_product=catalog, best_match=best, all_candidates=candidates)


class TestKeepCompleteOffers:
    def test_drops_offers_missing_price_or_shipping(self):
        complete = _candidate("alpha", "100.00", "0.00")
        no_shipping = _candidate("beta", "90.00", None)
        no_price = _candidate("gamma", None, "5.00")
        result = _result(complete, [complete, no_shipping, no_price])

        [filtered] = keep_complete_offers([result])

        assert filtered.all_candidates == [complete]

    def test_free_shipping_is_complete(self):
        free = _candidate("alpha", "100.00", "0.00")
        result = _result(free, [free])

        [filtered] = keep_complete_offers([result])

        assert filtered.all_candidates == [free]

    def test_recomputes_best_match_when_original_is_incomplete(self):
        incomplete_best = _candidate("alpha", "100.00", None, confidence=0.99)
        complete_low = _candidate("beta", "90.00", "5.00", confidence=0.70)
        complete_high = _candidate("gamma", "80.00", "0.00", confidence=0.85)
        result = _result(incomplete_best, [incomplete_best, complete_low, complete_high])

        [filtered] = keep_complete_offers([result])

        assert filtered.best_match == complete_high
        assert incomplete_best not in filtered.all_candidates

    def test_product_with_no_complete_offers_becomes_unmatched(self):
        no_shipping = _candidate("alpha", "90.00", None)
        result = _result(no_shipping, [no_shipping])

        [filtered] = keep_complete_offers([result])

        assert filtered.all_candidates == []
        assert filtered.best_match is None
        assert filtered.is_matched is False

    def test_keeps_original_best_match_when_still_complete(self):
        best = _candidate("alpha", "100.00", "0.00", confidence=0.80)
        other = _candidate("beta", "90.00", "5.00", confidence=0.95)
        result = _result(best, [best, other])

        [filtered] = keep_complete_offers([result])

        assert filtered.best_match == best

    def test_does_not_mutate_the_input(self):
        no_shipping = _candidate("alpha", "90.00", None)
        result = _result(no_shipping, [no_shipping])

        keep_complete_offers([result])

        assert result.all_candidates == [no_shipping]
        assert result.best_match == no_shipping
