"""Unit tests for the PDP verifier cascade (no network — MockTransport)."""

from decimal import Decimal

import httpx

from repricing_engine.models.enums import Availability, Market, MatchMethod
from repricing_engine.models.match_result import MatchResult
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


# Bodies padded past the fetcher's 1024-byte "usable HTML" floor.
_PAD = "<p>" + ("Fantini Cosmi ECOCOMFORT PLUS AP19993 recuperatore di calore. " * 30) + "</p>"

_JSONLD_WITH_SHIPPING = f"""
<html><head><script type="application/ld+json">
{{"@type":"Product","sku":"APL-IPH13-128","gtin13":"4006381333931",
 "offers":{{"@type":"Offer","price":"211.75","priceCurrency":"EUR",
   "availability":"https://schema.org/InStock",
   "shippingDetails":{{"@type":"OfferShippingDetails",
     "shippingRate":{{"@type":"MonetaryAmount","value":"8.53","currency":"EUR"}}}}}}}}
</script></head><body>iPhone{_PAD}</body></html>
"""

_FREE_SHIPPING_HTML = f"""
<html><body><h1>Prodotto</h1><p>Spedizione gratuita in 24 ore</p>
<script type="application/ld+json">{{"@type":"Product","sku":"APL-IPH13-128",
"offers":{{"@type":"Offer","price":"239.00","priceCurrency":"EUR"}}}}</script>{_PAD}</body></html>
"""

_CONDITIONAL_SHIPPING_HTML = f"""
<html><body><p>Spedizione gratuita sopra 99€</p>
<script type="application/ld+json">{{"@type":"Product","sku":"APL-IPH13-128",
"offers":{{"@type":"Offer","price":"49.00","priceCurrency":"EUR"}}}}</script>{_PAD}</body></html>
"""


class TestShippingExtraction:
    async def test_reads_structured_shipping_rate(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, text=_JSONLD_WITH_SHIPPING))
        async with client:
            verifier = _verifier(client)
            verified = await verifier.verify_candidates(_catalog(), [_candidate()], 15)
        offer = verified[0].competitor_product
        assert offer.shipping_cost == Decimal("8.53")
        assert offer.price == Decimal("211.75")

    async def test_free_shipping_text_sets_zero(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, text=_FREE_SHIPPING_HTML))
        async with client:
            verifier = _verifier(client)
            verified = await verifier.verify_candidates(_catalog(), [_candidate()], 15)
        assert verified[0].competitor_product.shipping_cost == Decimal("0")

    async def test_conditional_free_shipping_is_not_zeroed(self, mock_async_client):
        # "gratuita sopra 99€" is a threshold, not unconditional free shipping.
        client = mock_async_client(
            lambda request: httpx.Response(200, text=_CONDITIONAL_SHIPPING_HTML)
        )
        async with client:
            verifier = _verifier(client)
            verified = await verifier.verify_candidates(_catalog(), [_candidate(price=None)], 15)
        assert verified[0].competitor_product.shipping_cost is None


def _gtin_page(gtin: str, sku: str = "APL-IPH13-128") -> str:
    return f"""<html><body><h1>{sku}</h1>
<script type="application/ld+json">{{"@type":"Product","sku":"{sku}","gtin13":"{gtin}",
"offers":{{"@type":"Offer","price":"100.00","priceCurrency":"EUR"}}}}</script>{_PAD}</body></html>"""


def _catalog_no_barcode() -> CatalogProduct:
    return CatalogProduct(
        sku="APL-IPH13-128",
        ean=None,
        gtin=None,
        brand="Apple",
        title="x",
        category="",
        market=Market.IT,
    )


def _result(catalog: CatalogProduct, candidates: list[MatchCandidate]) -> MatchResult:
    return MatchResult(
        catalog_product=catalog,
        best_match=None,
        all_candidates=candidates,
        rejected_candidates=[],
        processing_time_ms=1.0,
    )


_META_PRICE_HTML = f"""
<html><head>
<meta property="product:price:amount" content="349.00">
<meta property="product:price:currency" content="EUR">
</head><body><h1>APL-IPH13-128</h1>
<script type="application/ld+json">{{"@type":"Product","sku":"APL-IPH13-128"}}</script>
{_PAD}</body></html>
"""


class TestMetaPriceFallback:
    async def test_reads_price_from_opengraph_meta(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, text=_META_PRICE_HTML))
        async with client:
            verifier = _verifier(client)
            verified = await verifier.verify_candidates(_catalog(), [_candidate()], 15)
        candidate = verified[0]
        assert candidate.competitor_product.price == Decimal("349.00")
        assert candidate.match_details["extraction_method"] == "meta"


class TestBarcodeRecovery:
    async def test_consensus_recovers_ean_and_upgrades_to_pdp_gtin(self, mock_async_client):
        gtin = "4006381333931"  # checksum-valid EAN-13
        client = mock_async_client(lambda request: httpx.Response(200, text=_gtin_page(gtin)))
        cat = _catalog_no_barcode()
        cands = [_candidate("https://a.it/p"), _candidate("https://b.it/p")]
        async with client:
            verifier = _verifier(client)
            out = await verifier.verify_result(cat, _result(cat, cands), 15)

        assert out.catalog_product.ean == gtin  # product_ean now filled from consensus
        assert all(c.match_details["confirmation_method"] == "PDP·GTIN" for c in out.all_candidates)
        assert all(c.match_details.get("gtin_recovered") for c in out.all_candidates)

    async def test_single_page_does_not_recover(self, mock_async_client):
        gtin = "4006381333931"
        client = mock_async_client(lambda request: httpx.Response(200, text=_gtin_page(gtin)))
        cat = _catalog_no_barcode()
        async with client:
            verifier = _verifier(client)
            out = await verifier.verify_result(
                cat, _result(cat, [_candidate("https://a.it/p")]), 15
            )

        assert out.catalog_product.ean is None  # only one page agrees -> not adopted
        assert out.all_candidates[0].match_details["confirmation_method"] == "PDP·SKU"

    def test_recover_flags_conflicting_barcode(self):
        cat = _catalog_no_barcode()

        def with_gtin(sid, page_gtin):
            base = _candidate(f"https://{sid}.it/p")
            return base.model_copy(
                update={"match_details": {**base.match_details, "page_gtin": page_gtin}}
            )

        cands = [
            with_gtin("a", "4006381333931"),
            with_gtin("b", "4006381333931"),  # consensus (2 agree)
            with_gtin("d", "5010029000108"),  # different barcode -> conflict
        ]
        recovered, out = PdpVerifier._recover_barcode(cat, cands)
        assert recovered.ean == "4006381333931"
        by_url = {c.competitor_product.url: c for c in out}
        assert by_url["https://a.it/p"].match_details["confirmation_method"] == "PDP·GTIN"
        assert by_url["https://d.it/p"].match_details.get("gtin_conflict") == "5010029000108"

    async def test_catalog_with_valid_ean_is_untouched(self, mock_async_client):
        client = mock_async_client(
            lambda request: httpx.Response(200, text=_gtin_page("4006381333931"))
        )
        cat = _catalog()  # already has a valid EAN
        cands = [_candidate("https://a.it/p"), _candidate("https://b.it/p")]
        async with client:
            verifier = _verifier(client)
            out = await verifier.verify_result(cat, _result(cat, cands), 15)

        assert out.catalog_product.ean == "4006381333931"  # unchanged (its own EAN)


class TestApplyExtractionCurrency:
    @staticmethod
    def _offer() -> CompetitorProduct:
        return CompetitorProduct(
            source="searxng",
            source_id="X",
            title="Geberit Sigma20",
            price=None,
            currency="EUR",
            url="https://shop.hu/p",
            market=Market.IT,
            source_provider="searxng",
        )

    def test_foreign_currency_price_is_not_applied(self):
        """A HUF/SEK/... page price must not be mislabeled as the offer's EUR price."""
        foreign = PdpExtractionResult(price=Decimal("24790.00"), currency="HUF")
        updated = PdpVerifier._apply_extraction(self._offer(), foreign)
        assert updated.price is None
        assert updated.currency == "EUR"

    def test_same_currency_price_is_applied(self):
        same = PdpExtractionResult(price=Decimal("249.00"), currency="EUR")
        updated = PdpVerifier._apply_extraction(self._offer(), same)
        assert updated.price == Decimal("249.00")
        assert updated.currency == "EUR"

    def test_currencyless_price_is_applied(self):
        """JSON-LD often omits currency; assume the offer's own currency."""
        no_cur = PdpExtractionResult(price=Decimal("199.00"), currency=None)
        updated = PdpVerifier._apply_extraction(self._offer(), no_cur)
        assert updated.price == Decimal("199.00")
