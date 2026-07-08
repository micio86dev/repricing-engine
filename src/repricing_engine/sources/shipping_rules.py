"""Curated per-retailer free-shipping rules (an allow-list to extend as verified).

Some Italian shops in this product class ship free within IT but expose no
structured shipping data on their product pages, so the PDP shipping cascade
reads nothing. This module is a conservative, manually-verified allow-list: a
domain is added ONLY once its free shipping has been confirmed in the project's
real output. Extend it as more retailers are verified — never guess.
"""

from __future__ import annotations

# Italian retailers confirmed to ship free within IT for this product class.
# Seeded from domains already observed shipping free in the project's output.
FREE_IT_SHIPPING_DOMAINS: frozenset[str] = frozenset(
    {
        "idrocrimart.it",
        "complementiclimatici.it",
        "sovatem.it",
        "climaprice.it",
        "elmaxweb.it",
    }
)


def free_shipping_for(domain: str) -> bool:
    """Return ``True`` when ``domain`` is a curated free-shipping retailer.

    Args:
        domain: A host (with or without a leading ``www.``, any case).

    Returns:
        ``True`` if the normalized domain is in :data:`FREE_IT_SHIPPING_DOMAINS`.
    """
    if not domain:
        return False
    normalized = domain.strip().lower()
    if normalized.startswith("www."):
        normalized = normalized[4:]
    return normalized in FREE_IT_SHIPPING_DOMAINS
