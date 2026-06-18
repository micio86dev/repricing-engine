"""Deduplicate raw search results across providers.

Two passes, in order:

1. **Same URL** (after stripping tracking params) -> keep the *richer* record
   (the one carrying a price / shipping / identifiers).
2. **Same domain** -> keep a single best offer (the lowest known price; the
   richest record when no price is known).

Collapsing to one offer per domain is intentional: the competitor landscape
wants one row per retailer, and per-product results all describe the same item.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

if TYPE_CHECKING:
    from repricing_engine.sources.models import RawSearchResult

# Query parameters that never identify a product (tracking / affiliate noise).
_TRACKING_PARAM_PREFIXES: tuple[str, ...] = ("utm_",)
_TRACKING_PARAM_NAMES: frozenset[str] = frozenset(
    {"ref", "tag", "gclid", "fbclid", "msclkid", "mc_eid", "mc_cid", "_ga"}
)


def _is_tracking_param(name: str) -> bool:
    lowered = name.lower()
    if lowered in _TRACKING_PARAM_NAMES:
        return True
    return any(lowered.startswith(prefix) for prefix in _TRACKING_PARAM_PREFIXES)


def normalize_url(url: str) -> str:
    """Canonicalize a URL for dedup: drop tracking params, fragment, trailing slash."""
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    kept = [(k, v) for k, v in parse_qsl(parsed.query) if not _is_tracking_param(k)]
    query = urlencode(sorted(kept))
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme.lower() or "https", host, path, "", query, ""))


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _richness(result: RawSearchResult) -> int:
    """Score how much usable data a result carries (higher == richer)."""
    score = 0
    if result.price is not None:
        score += 4
    if result.shipping_cost is not None:
        score += 2
    if result.ean or result.sku:
        score += 2
    if result.snippet:
        score += 1
    return score


def _prefer(current: RawSearchResult, candidate: RawSearchResult) -> RawSearchResult:
    """Pick the better of two results for the *same URL* (richer wins)."""
    return candidate if _richness(candidate) > _richness(current) else current


def _best_for_domain(results: list[RawSearchResult]) -> RawSearchResult:
    """Pick one offer for a domain: lowest known price, else the richest."""
    priced = [r for r in results if r.price is not None]
    if priced:
        return min(priced, key=lambda r: (r.price, -_richness(r)))
    return max(results, key=_richness)


def deduplicate(results: list[RawSearchResult]) -> list[RawSearchResult]:
    """Deduplicate raw results by URL then by domain.

    Args:
        results: Raw results gathered from one or more providers.

    Returns:
        One result per distinct retailer domain, order-stable by first appearance.
    """
    by_url: dict[str, RawSearchResult] = {}
    url_order: list[str] = []
    for result in results:
        key = normalize_url(result.url)
        if key in by_url:
            by_url[key] = _prefer(by_url[key], result)
        else:
            by_url[key] = result
            url_order.append(key)

    by_domain: dict[str, list[RawSearchResult]] = {}
    domain_order: list[str] = []
    for key in url_order:
        result = by_url[key]
        domain = _domain(result.url)
        if domain not in by_domain:
            by_domain[domain] = []
            domain_order.append(domain)
        by_domain[domain].append(result)

    return [_best_for_domain(by_domain[domain]) for domain in domain_order]
