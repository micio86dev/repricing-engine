"""Confirm catalog identifiers/brand against a raw search result.

SERP providers return a title/snippet/URL but no structured identifiers, so a
fetched offer can normally only be matched by the (noisy) semantic layer. This
module closes that gap *truthfully*: when the catalog product's SKU, EAN/GTIN, or
brand **literally appears** in a result's title/snippet/URL, we stamp that value
onto the :class:`RawSearchResult`. The downstream mapper then carries it into the
``CompetitorProduct``, so the deterministic ``exact_id`` / ``sku_brand`` layers
can confirm the offer with high confidence — because the page really references
the identifier, not because we assumed it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repricing_engine.normalization.identifiers import normalize_ean, normalize_gtin
from repricing_engine.normalization.text import normalize_brand

if TYPE_CHECKING:
    from repricing_engine.models.product import CatalogProduct
    from repricing_engine.sources.models import RawSearchResult

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")
_NON_DIGIT_RE = re.compile(r"\D")

# Minimum length of an alphanumeric SKU we trust for substring confirmation
# (short codes are too collision-prone to confirm from free text).
_MIN_SKU_ALNUM_LEN = 5


def _alnum(value: str) -> str:
    """Lower-case and strip everything but ``[a-z0-9]`` (separator-insensitive)."""
    return _NON_ALNUM_RE.sub("", value.lower())


def confirm_identifiers(result: RawSearchResult, product: CatalogProduct) -> RawSearchResult:
    """Return ``result`` with catalog SKU/EAN/GTIN/brand stamped where confirmed.

    A value is stamped only when it is present in the result's text; otherwise the
    field is left as-is. The result is frozen, so a copy is returned.

    Args:
        result: The raw search result to enrich.
        product: The catalog product this search was run for.

    Returns:
        A (possibly new) :class:`RawSearchResult` with confirmed fields set.
    """
    text = " ".join(part for part in (result.title, result.snippet, result.url) if part)
    updates: dict[str, str] = {}

    if result.sku is None and _sku_present(product.sku, text):
        updates["sku"] = product.sku

    if result.ean is None:
        confirmed_id = _confirmed_barcode(product, text)
        if confirmed_id is not None:
            updates["ean"] = confirmed_id

    if result.brand is None and _brand_present(product.brand, text):
        updates["brand"] = product.brand

    return result.model_copy(update=updates) if updates else result


def _sku_present(sku: str | None, text: str) -> bool:
    """True when ``sku`` appears in ``text`` (separator-insensitive substring)."""
    if not sku:
        return False
    needle = _alnum(sku)
    if len(needle) < _MIN_SKU_ALNUM_LEN:
        return False
    return needle in _alnum(text)


def _confirmed_barcode(product: CatalogProduct, text: str) -> str | None:
    """Return the catalog EAN/GTIN if a valid one appears verbatim in ``text``."""
    haystack = _NON_DIGIT_RE.sub("", text)
    for candidate in (normalize_ean(product.ean), normalize_gtin(product.gtin)):
        if candidate is not None and candidate in haystack:
            return candidate
    return None


def _brand_present(brand: str | None, text: str) -> bool:
    """True when the (normalized) brand appears in ``text``."""
    normalized = normalize_brand(brand)
    if not normalized:
        return False
    return normalized in text.lower()
