"""Unit tests for the PDP AI extractor (injected FakeGroqClient — no network)."""

from decimal import Decimal

from repricing_engine.models.enums import Availability, Market
from repricing_engine.models.product import CatalogProduct
from repricing_engine.pdp.extractor import AiExtractor
from tests.conftest import FakeGroqClient


def _catalog() -> CatalogProduct:
    return CatalogProduct(
        sku="APL-IPH13-128",
        ean="4006381333931",
        brand="Apple",
        title="Apple iPhone 13 128GB Blue",
        category="Smartphones",
        market=Market.IT,
    )


class TestAiExtractor:
    async def test_extracts_offer(self):
        payload = {
            "price": 789.0,
            "currency": "eur",
            "shipping_cost": 0,
            "availability": "in stock",
            "seller": "ShopX",
            "confidence": 0.8,
        }
        extractor = AiExtractor(FakeGroqClient(payload=payload))
        result = await extractor.extract("<html><body>price 789</body></html>", _catalog(), "u")
        assert result is not None
        assert result.price == Decimal("789.0")
        assert result.currency == "EUR"
        assert result.shipping_cost == Decimal("0")
        assert result.availability is Availability.IN_STOCK
        assert result.seller == "ShopX"
        assert result.confidence == 0.8

    async def test_failure_returns_none(self):
        extractor = AiExtractor(FakeGroqClient(error=RuntimeError("groq down")))
        result = await extractor.extract("<html></html>", _catalog(), "u")
        assert result is None

    def test_clean_html_strips_noise_and_truncates(self):
        html = (
            "<html><head><style>.x{}</style></head><body>"
            "<nav>menu</nav><main>" + ("word " * 2000) + "</main>"
            "<footer>foot</footer></body></html>"
        )
        text = AiExtractor._clean_html(html)
        assert "menu" not in text
        assert "foot" not in text
        assert len(text) <= 4000
