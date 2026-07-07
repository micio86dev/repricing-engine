"""Unit tests for the shared brand+title search-query helper."""

from repricing_engine.models.enums import Market
from repricing_engine.models.product import CatalogProduct
from repricing_engine.sources.query import brand_title_query


def _product(brand: str, title: str) -> CatalogProduct:
    return CatalogProduct(sku="SKU1", brand=brand, title=title, category="Misc", market=Market.IT)


def test_drops_brand_when_title_already_leads_with_it():
    assert brand_title_query(_product("Geberit", "Geberit Sigma 8")) == "Geberit Sigma 8"


def test_prepends_brand_when_title_lacks_it():
    assert brand_title_query(_product("Geberit", "Sigma 8")) == "Geberit Sigma 8"


def test_leading_brand_match_is_case_insensitive_but_keeps_title_casing():
    assert brand_title_query(_product("geberit", "GEBERIT Sigma 8")) == "GEBERIT Sigma 8"


def test_title_only_when_brand_empty():
    assert brand_title_query(_product("", "Sigma 8")) == "Sigma 8"


def test_brand_only_when_title_empty():
    assert brand_title_query(_product("Geberit", "")) == "Geberit"


def test_partial_word_brand_is_not_treated_as_prefix():
    # "Geb" must not be stripped from "Geberit ..." — only a whole leading token counts.
    assert brand_title_query(_product("Geb", "Geberit Sigma 8")) == "Geb Geberit Sigma 8"
