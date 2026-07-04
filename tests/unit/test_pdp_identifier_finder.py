"""Unit tests for the PDP identifier finder (regex + meta + JSON-LD)."""

from repricing_engine.models.enums import Market
from repricing_engine.models.product import CatalogProduct
from repricing_engine.pdp.identifier_finder import IdentifierFinder


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


class TestIdentifierFinder:
    def test_finds_identifiers_and_json_ld(self, fixture_text):
        html = fixture_text("pdp_with_jsonld.html")
        findings = IdentifierFinder().find_identifiers(html, _catalog())
        assert findings.gtin_found is True
        assert findings.ean_found is True
        assert findings.sku_found is True
        assert findings.any_found is True
        assert findings.has_json_ld is True
        assert findings.json_ld_data["price"] == "789.00"
        assert findings.json_ld_data["currency"] == "EUR"
        assert "InStock" in findings.json_ld_data["availability"]
        assert findings.json_ld_data["sku"] == "APL-IPH13-128"
        assert findings.json_ld_data["gtin"] == "4006381333931"
        assert findings.json_ld_data["brand"] == "Apple"
        assert findings.json_ld_data["seller"] == "ShopAlpha"

    def test_sku_only_inline(self):
        html = "<html><body><p>Cod. art.: APL-IPH13-128 in pronta consegna</p></body></html>"
        findings = IdentifierFinder().find_identifiers(html, _catalog())
        assert findings.sku_found is True
        assert findings.gtin_found is False
        assert findings.has_json_ld is False

    def test_no_identifiers(self):
        html = "<html><body><p>A completely unrelated product</p></body></html>"
        findings = IdentifierFinder().find_identifiers(html, _catalog())
        assert findings.any_found is False
        assert findings.found_identifiers == []

    def test_malformed_json_ld_skipped(self):
        html = (
            '<html><head><script type="application/ld+json">{ not valid json,,, '
            "</script></head><body>APL-IPH13-128</body></html>"
        )
        findings = IdentifierFinder().find_identifiers(html, _catalog())
        assert findings.has_json_ld is False
        assert findings.sku_found is True  # still found in body text

    def test_graph_wrapped_product(self):
        html = (
            '<html><head><script type="application/ld+json">'
            '{"@context":"https://schema.org","@graph":['
            '{"@type":"BreadcrumbList"},'
            '{"@type":"Product","sku":"APL-IPH13-128",'
            '"offers":{"@type":"Offer","price":"699.00","priceCurrency":"EUR"}}'
            "]}</script></head><body>product</body></html>"
        )
        findings = IdentifierFinder().find_identifiers(html, _catalog())
        assert findings.has_json_ld is True
        assert findings.json_ld_data["price"] == "699.00"

    def test_type_list_product(self):
        html = (
            '<html><head><script type="application/ld+json">'
            '{"@type":["Product","IndividualProduct"],"sku":"APL-IPH13-128"}'
            "</script></head><body>x</body></html>"
        )
        findings = IdentifierFinder().find_identifiers(html, _catalog())
        assert findings.json_ld_data["sku"] == "APL-IPH13-128"
