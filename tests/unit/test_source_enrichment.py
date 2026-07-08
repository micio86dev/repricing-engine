"""Unit tests for identifier/brand confirmation on raw search results."""

from repricing_engine.models.enums import Market
from repricing_engine.models.product import CatalogProduct
from repricing_engine.sources.enrichment import confirm_identifiers
from repricing_engine.sources.models import RawSearchResult


def _catalog(sku: str = "110.791.00.1", ean: str | None = None) -> CatalogProduct:
    return CatalogProduct(
        sku=sku,
        ean=ean,
        brand="Geberit",
        title="Geberit Sigma 8 Cassetta",
        category="",
        market=Market.IT,
    )


def _result(title: str = "", snippet: str | None = None, url: str = "https://shop.it/p"):
    return RawSearchResult(source_provider="searxng", title=title, url=url, snippet=snippet)


def test_stamps_sku_when_present_in_snippet():
    product = _catalog()
    result = _result(title="Modulo Geberit", snippet="codice 110.791.00.1 per wc sospeso")
    enriched = confirm_identifiers(result, product)
    assert enriched.sku == "110.791.00.1"  # confirmed on the page -> stamped
    assert enriched.brand == "Geberit"  # brand also present


def test_sku_match_is_separator_insensitive():
    product = _catalog(sku="110.791.00.1")
    # The page renders the code without dots — still a confirmation.
    result = _result(title="Geberit art 110 791 00 1")
    assert confirm_identifiers(result, product).sku == "110.791.00.1"


def test_does_not_stamp_absent_sku():
    product = _catalog(sku="AP19993")
    result = _result(title="Unrelated ventilation unit", snippet="no code here")
    enriched = confirm_identifiers(result, product)
    assert enriched.sku is None
    assert enriched.brand is None


def test_stamps_valid_ean_present_in_text():
    product = _catalog(sku="X", ean="4006381333931")
    result = _result(title="Widget", snippet="EAN 4006381333931 in stock")
    assert confirm_identifiers(result, product).ean == "4006381333931"


def test_ignores_corrupt_ean_even_if_digits_appear():
    # Excel-mangled EAN fails checksum -> never stamped, even if a number is present.
    product = _catalog(sku="X", ean="8,02586E+12")
    result = _result(title="Something", snippet="8025860000000")
    assert confirm_identifiers(result, product).ean is None


def test_returns_same_instance_when_nothing_confirmed():
    product = _catalog(sku="AP19993")
    result = _result(title="totally unrelated")
    assert confirm_identifiers(result, product) is result


def test_does_not_overwrite_existing_fields():
    product = _catalog()
    result = RawSearchResult(
        source_provider="trovaprezzi",
        title="Geberit 110.791.00.1",
        url="https://x.it/p",
        sku="EXISTING-SKU",
        brand="AlreadySet",
    )
    enriched = confirm_identifiers(result, product)
    assert enriched.sku == "EXISTING-SKU"
    assert enriched.brand == "AlreadySet"


def test_short_codes_are_not_confirmed():
    # A 4-char code is too collision-prone to confirm from free text.
    product = _catalog(sku="AB12")
    result = _result(title="model AB12 here")
    assert confirm_identifiers(result, product).sku is None
