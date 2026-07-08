"""Shared search-query construction for source providers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from repricing_engine.models.product import CatalogProduct


def brand_title_query(product: CatalogProduct) -> str:
    """Build a ``brand + title`` keyword query without repeating the brand.

    Catalog titles frequently already start with the brand ("Geberit Sigma 8"),
    so blindly prefixing the brand produces "Geberit Geberit Sigma 8". This drops
    the brand when the title already leads with it as a whole word (case-
    insensitive), and prepends it otherwise.

    Args:
        product: The catalog product to search for.

    Returns:
        The de-duplicated ``brand title`` query (or whichever part is present).
    """
    brand = (product.brand or "").strip()
    title = (product.title or "").strip()
    if not brand:
        return title
    if not title:
        return brand
    lower_title, lower_brand = title.lower(), brand.lower()
    if lower_title == lower_brand or lower_title.startswith(f"{lower_brand} "):
        return title
    return f"{brand} {title}"
