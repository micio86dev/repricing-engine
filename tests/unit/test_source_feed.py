"""Unit tests for the merchant FEED source provider (no network — MockTransport)."""

from decimal import Decimal

import httpx

from repricing_engine.models.enums import Market
from repricing_engine.models.product import CatalogProduct
from repricing_engine.sources.providers.feed import FeedProvider

_FEED_URL = "https://partner.example.com/feed.xml"
_OTHER_FEED_URL = "https://other.example.com/feed.xml"

# A minimal Google-Merchant RSS 2.0 feed: two <item>s carrying price + shipping
# natively via the ``g:`` namespace.
_FEED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:g="http://base.google.com/ns/1.0" version="2.0">
  <channel>
    <title>Example Merchant Feed</title>
    <link>https://www.shop-a.it</link>
    <description>Test product feed</description>
    <item>
      <title>Apple iPhone 13 128GB Blue</title>
      <link>https://www.shop-a.it/iphone-13</link>
      <g:price>211.75 EUR</g:price>
      <g:shipping>
        <g:country>IT</g:country>
        <g:service>Standard</g:service>
        <g:price>5.90 EUR</g:price>
      </g:shipping>
      <g:gtin>4006381333931</g:gtin>
      <g:availability>in stock</g:availability>
    </item>
    <item>
      <title>Widget SKU-ABC-123 Pro Edition</title>
      <link>https://www.shop-b.de/widget-pro</link>
      <g:price>49.99 EUR</g:price>
      <g:shipping>
        <g:country>DE</g:country>
        <g:price>0.00 EUR</g:price>
      </g:shipping>
      <g:gtin>0885909950805</g:gtin>
      <g:availability>in stock</g:availability>
    </item>
  </channel>
</rss>
"""


def _catalog_with_ean() -> CatalogProduct:
    return CatalogProduct(
        sku="APL-IPH13-128",
        ean="4006381333931",
        brand="Apple",
        title="Apple iPhone 13 128GB Blue",
        category="Smartphones",
        market=Market.IT,
    )


def _catalog_matched_by_sku() -> CatalogProduct:
    # No EAN and a title that shares no full token set with the feed item, so the
    # only possible match is the SKU appearing verbatim in the item title.
    return CatalogProduct(
        sku="SKU-ABC-123",
        brand="Widgetco",
        title="Gizmo Deluxe",
        category="Gadgets",
        market=Market.DE,
    )


def _feed_handler(counter: dict[str, int] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter["hits"] = counter.get("hits", 0) + 1
        return httpx.Response(200, content=_FEED_XML, headers={"content-type": "application/xml"})

    return handler


class TestFeedSearch:
    async def test_ean_match_reads_price_and_shipping(self, mock_async_client):
        client = mock_async_client(_feed_handler())
        async with client:
            provider = FeedProvider(client, [_FEED_URL])
            results = await provider.search(_catalog_with_ean(), Market.IT)

        assert len(results) == 1
        result = results[0]
        assert result.source_provider == "feed"
        assert result.title == "Apple iPhone 13 128GB Blue"
        assert result.url == "https://www.shop-a.it/iphone-13"
        assert result.price == Decimal("211.75")
        assert result.shipping_cost == Decimal("5.90")
        assert result.currency == "EUR"
        assert result.domain == "shop-a.it"
        assert result.ean == "4006381333931"

    async def test_free_shipping_zero_is_decimal_zero(self, mock_async_client):
        client = mock_async_client(_feed_handler())
        async with client:
            provider = FeedProvider(client, [_FEED_URL])
            results = await provider.search(_catalog_matched_by_sku(), Market.DE)

        assert len(results) == 1
        assert results[0].shipping_cost == Decimal("0")

    async def test_sku_in_title_match_when_gtin_differs(self, mock_async_client):
        client = mock_async_client(_feed_handler())
        async with client:
            provider = FeedProvider(client, [_FEED_URL])
            results = await provider.search(_catalog_matched_by_sku(), Market.DE)

        assert len(results) == 1
        result = results[0]
        assert result.title == "Widget SKU-ABC-123 Pro Edition"
        assert result.url == "https://www.shop-b.de/widget-pro"
        assert result.price == Decimal("49.99")

    async def test_feed_is_fetched_only_once_across_searches(self, mock_async_client):
        counter: dict[str, int] = {}
        client = mock_async_client(_feed_handler(counter))
        async with client:
            provider = FeedProvider(client, [_FEED_URL])
            await provider.search(_catalog_with_ean(), Market.IT)
            await provider.search(_catalog_matched_by_sku(), Market.DE)

        assert counter["hits"] == 1

    async def test_malformed_feed_yields_empty_and_does_not_raise(self, mock_async_client):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<rss><channel><item>broken")

        client = mock_async_client(handler)
        async with client:
            provider = FeedProvider(client, [_FEED_URL])
            results = await provider.search(_catalog_with_ean(), Market.IT)

        assert results == []

    async def test_one_bad_feed_does_not_sink_the_others(self, mock_async_client):
        def handler(request: httpx.Request) -> httpx.Response:
            if _OTHER_FEED_URL in str(request.url):
                return httpx.Response(500)
            return httpx.Response(200, content=_FEED_XML)

        client = mock_async_client(handler)
        async with client:
            provider = FeedProvider(client, [_OTHER_FEED_URL, _FEED_URL])
            results = await provider.search(_catalog_with_ean(), Market.IT)

        assert len(results) == 1
        assert results[0].price == Decimal("211.75")


class TestFeedAvailability:
    def test_available_requires_enabled_and_urls(self, mock_async_client):
        client = mock_async_client(lambda request: httpx.Response(200, content=_FEED_XML))
        assert FeedProvider(client, [_FEED_URL]).is_available() is True
        assert FeedProvider(client, []).is_available() is False
        assert FeedProvider(client, [_FEED_URL], enabled=False).is_available() is False
