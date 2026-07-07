"""IdentifierFinder — confirm catalog identifiers on a page and read JSON-LD.

Entirely free (no API calls): a word-boundary scan for the catalog EAN/GTIN/SKU,
a sweep of product ``<meta>`` tags, and a parse of any ``application/ld+json``
Product block. This is what makes the ``PDP·EAN/GTIN/SKU`` and ``json_ld``
confirmation labels truthful, and it often yields the price/shipping/stock for
free — sparing the AI extractor.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from bs4 import BeautifulSoup

from repricing_engine.pdp.models import IdentifierFindings

if TYPE_CHECKING:
    from repricing_engine.models.product import CatalogProduct

logger = logging.getLogger(__name__)

# schema.org keys that may hold a GTIN on a Product node.
_JSON_LD_GTIN_KEYS: tuple[str, ...] = ("gtin13", "gtin14", "gtin12", "gtin8", "gtin")


class IdentifierFinder:
    """Locate catalog identifiers and structured offer data on a fetched page."""

    def find_identifiers(self, html: str, catalog_product: CatalogProduct) -> IdentifierFindings:
        """Scan ``html`` for the catalog product's identifiers and JSON-LD.

        Args:
            html: The fetched page HTML.
            catalog_product: The product whose identifiers we expect to find.

        Returns:
            An :class:`IdentifierFindings` recording which identifiers matched
            and any parsed Product JSON-LD.
        """
        soup = BeautifulSoup(html, "lxml")
        haystacks = self._haystacks(soup, html)
        json_ld = self._parse_json_ld(soup)
        meta_price, meta_currency = self._price_from_meta_microdata(soup)
        meta_shipping = self._shipping_from_meta_microdata(soup)

        found: list[str] = []
        ean_found = self._contains_identifier(catalog_product.ean, haystacks, json_ld)
        gtin_found = self._contains_identifier(catalog_product.gtin, haystacks, json_ld)
        sku_found = self._contains_sku(catalog_product.sku, haystacks, json_ld)

        if ean_found and catalog_product.ean:
            found.append(catalog_product.ean)
        if gtin_found and catalog_product.gtin:
            found.append(catalog_product.gtin)
        if sku_found and catalog_product.sku:
            found.append(catalog_product.sku)

        return IdentifierFindings(
            ean_found=ean_found,
            gtin_found=gtin_found,
            sku_found=sku_found,
            found_identifiers=found,
            json_ld_data=json_ld,
            meta_price=meta_price,
            meta_currency=meta_currency,
            meta_shipping=meta_shipping,
        )

    # OpenGraph / product price meta — page-level and reliable. We deliberately avoid
    # generic ``itemprop="price"`` microdata: it also appears on financing widgets and
    # related-product blocks, producing bogus values (e.g. "€1.00"). Precision on real
    # prices matters more than squeezing out a few extra low-confidence ones.
    _META_PRICE_ATTRS: tuple[dict[str, str], ...] = (
        {"property": "product:price:amount"},
        {"property": "og:price:amount"},
    )
    _META_CURRENCY_ATTRS: tuple[dict[str, str], ...] = (
        {"property": "product:price:currency"},
        {"property": "og:price:currency"},
    )
    # Page-level shipping meta (rare but reliable when present).
    _META_SHIPPING_ATTRS: tuple[dict[str, str], ...] = (
        {"property": "product:shipping:amount"},
        {"property": "product:shipping_cost:amount"},
        {"property": "og:shipping:amount"},
    )

    def _price_from_meta_microdata(self, soup: BeautifulSoup) -> tuple[str | None, str | None]:
        """Read a price/currency from OpenGraph/product ``<meta>`` tags (free, reliable)."""
        price = self._meta_content(soup, self._META_PRICE_ATTRS)
        currency = self._meta_content(soup, self._META_CURRENCY_ATTRS)
        return price, (currency.upper() if currency else None)

    def _shipping_from_meta_microdata(self, soup: BeautifulSoup) -> str | None:
        """Read a shipping cost from OpenGraph/product ``<meta>`` tags, if present."""
        return self._meta_content(soup, self._META_SHIPPING_ATTRS)

    @staticmethod
    def _meta_content(soup: BeautifulSoup, attr_sets: tuple[dict[str, str], ...]) -> str | None:
        """Return the first non-empty ``<meta content=...>`` for the given attrs."""
        for attrs in attr_sets:
            element = soup.find("meta", attrs=attrs)
            if element and element.get("content"):
                return str(element["content"]).strip()
        return None

    @staticmethod
    def _haystacks(soup: BeautifulSoup, html: str) -> tuple[str, str]:
        """Return (raw HTML, visible+meta text) to search for identifiers."""
        text_parts = [soup.get_text(" ", strip=True)]
        for meta in soup.find_all("meta"):
            content = meta.get("content")
            if content:
                text_parts.append(str(content))
        return html, " ".join(text_parts)

    @staticmethod
    def _contains_identifier(
        identifier: str | None,
        haystacks: tuple[str, str],
        json_ld: dict[str, Any],
    ) -> bool:
        """Word-boundary search for a numeric identifier (EAN/GTIN)."""
        if not identifier:
            return False
        pattern = re.compile(rf"\b{re.escape(identifier)}\b")
        if any(pattern.search(hay) for hay in haystacks):
            return True
        return identifier in {str(v) for v in json_ld.values()}

    @staticmethod
    def _contains_sku(
        sku: str | None,
        haystacks: tuple[str, str],
        json_ld: dict[str, Any],
    ) -> bool:
        """Case-insensitive search for a SKU (which may contain separators)."""
        if not sku:
            return False
        pattern = re.compile(re.escape(sku), re.IGNORECASE)
        if any(pattern.search(hay) for hay in haystacks):
            return True
        json_sku = str(json_ld.get("sku", ""))
        return bool(json_sku) and json_sku.casefold() == sku.casefold()

    def _parse_json_ld(self, soup: BeautifulSoup) -> dict[str, Any]:
        """Extract a normalized Product node from any JSON-LD on the page."""
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                logger.debug("Skipping malformed JSON-LD block.")
                continue
            product = self._find_product_node(data)
            if product is not None:
                return self._normalize_product(product)
        return {}

    def _find_product_node(self, data: object) -> dict[str, Any] | None:
        """Walk a JSON-LD payload (dict / list / @graph) for a Product node."""
        if isinstance(data, list):
            for item in data:
                found = self._find_product_node(item)
                if found is not None:
                    return found
            return None
        if isinstance(data, dict):
            if "@graph" in data:
                return self._find_product_node(data["@graph"])
            if self._is_product(data.get("@type")):
                return data
        return None

    @staticmethod
    def _is_product(type_value: object) -> bool:
        """True when a JSON-LD ``@type`` denotes a Product."""
        if isinstance(type_value, str):
            return type_value.casefold() == "product"
        if isinstance(type_value, list):
            return any(str(t).casefold() == "product" for t in type_value)
        return False

    def _normalize_product(self, product: dict[str, Any]) -> dict[str, Any]:
        """Flatten a Product JSON-LD node to a small, stable dict."""
        normalized: dict[str, Any] = {}
        if sku := product.get("sku"):
            normalized["sku"] = str(sku)
        for key in _JSON_LD_GTIN_KEYS:
            if value := product.get(key):
                normalized["gtin"] = str(value)
                break
        brand = product.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")
        if brand:
            normalized["brand"] = str(brand)

        offer = self._first_offer(product.get("offers"))
        if offer:
            # Plain Offer.price, else AggregateOffer.lowPrice (a listing's cheapest).
            price = offer.get("price")
            if price is None:
                price = offer.get("lowPrice")
            if price is not None:
                normalized["price"] = str(price)
            if currency := offer.get("priceCurrency"):
                normalized["currency"] = str(currency)
            if availability := offer.get("availability"):
                normalized["availability"] = str(availability)
            seller = offer.get("seller")
            if isinstance(seller, dict) and (seller_name := seller.get("name")):
                normalized["seller"] = str(seller_name)
            shipping = self._shipping_from_offer(offer)
            if shipping is not None:
                normalized["shipping"] = shipping
            stock = self._stock_from_offer(offer)
            if stock is not None:
                normalized["stock_quantity"] = stock
        return normalized

    @staticmethod
    def _stock_from_offer(offer: dict[str, Any]) -> str | None:
        """Read a stock quantity from a schema.org Offer's ``inventoryLevel``.

        ``inventoryLevel`` may be a ``QuantitativeValue`` (its ``value`` holds the
        count) or a bare number. Returns the count as a string (like the other
        normalized JSON-LD values), or ``None`` when the offer carries no stock.
        """
        level = offer.get("inventoryLevel")
        if isinstance(level, dict):
            level = level.get("value")
        return str(level) if level is not None else None

    @staticmethod
    def _shipping_from_offer(offer: dict[str, Any]) -> str | None:
        """Read a shipping cost from a schema.org ``OfferShippingDetails`` block.

        Returns the ``shippingRate.value`` (e.g. ``"0"`` for free shipping) as a
        string, or ``None`` when the offer carries no structured shipping.
        """
        details = offer.get("shippingDetails")
        if isinstance(details, list):
            details = next((d for d in details if isinstance(d, dict)), None)
        if not isinstance(details, dict):
            return None
        rate = details.get("shippingRate")
        if not isinstance(rate, dict):
            return None
        value = rate.get("value")
        return str(value) if value is not None else None

    @staticmethod
    def _first_offer(offers: object) -> dict[str, Any] | None:
        """Return the first offer node from an ``offers`` value (dict or list)."""
        if isinstance(offers, dict):
            return offers
        if isinstance(offers, list):
            for offer in offers:
                if isinstance(offer, dict):
                    return offer
        return None
