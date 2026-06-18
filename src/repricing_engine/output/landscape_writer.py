"""Write the competitor price landscape: one row per confirmed offer, plus a
derived per-product summary.

Where :class:`~repricing_engine.output.csv_writer.CsvWriter` emits a single best
match per catalog product, this writer emits *every* confirmed offer — the shape
a repricing workflow needs — and a per-product summary (competitor count, min /
median / max landed price, our position, and a suggested price floored at COGS).
"""

from __future__ import annotations

import json
from decimal import Decimal
from statistics import median
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import polars as pl

from repricing_engine.matching.classification import (
    confidence_tier_for,
    confirmation_method_for,
    match_field_for,
)
from repricing_engine.models.enums import Availability
from repricing_engine.output.csv_writer import CsvWriter

if TYPE_CHECKING:
    from pathlib import Path

    from repricing_engine.models.match_result import MatchResult
    from repricing_engine.models.product import CatalogProduct, MatchCandidate

# Raw offer-level columns (the client-agreed schema).
RAW_COLUMNS: tuple[str, ...] = (
    "product_sku",
    "product_ean",
    "product_name",
    "product_brand",
    "our_price",
    "our_cogs",
    "currency",
    "competitor_name",
    "competitor_domain",
    "competitor_url",
    "competitor_price",
    "competitor_shipping",
    "competitor_landed",
    "confidence",
    "match_field",
    "in_stock",
    "source",
    "scraped_at",
    # Appended for multi-source / PDP verification (blank in the CSV-only path).
    "confirmation_method",
    "pdp_verified",
    "source_provider",
    "extraction_method",
    "shipping_note",
)
# Derived per-product summary columns.
SUMMARY_COLUMNS: tuple[str, ...] = (
    "product_sku",
    "product_name",
    "product_brand",
    "our_price",
    "currency",
    "n_competitor",
    "min_landed",
    "median_landed",
    "max_landed",
    "cheapest_competitor",
    "cheapest_url",
    "position",
    "suggested_price",
)

_CENTS = Decimal("0.01")
_IN_STOCK_LABELS: dict[Availability, str] = {
    Availability.IN_STOCK: "yes",
    Availability.OUT_OF_STOCK: "no",
    Availability.PREORDER: "preorder",
    Availability.UNKNOWN: "unknown",
}


def _q(value: Decimal) -> str:
    """Quantize a money value to 2 decimals and stringify."""
    return str(value.quantize(_CENTS))


def _domain(url: str) -> str:
    """Return the bare host (without a leading ``www.``) of a URL."""
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _landed(candidate: MatchCandidate) -> Decimal | None:
    """Shipping-inclusive price of a candidate's offer, or ``None`` if unpriced."""
    competitor = candidate.competitor_product
    if competitor.price is None:
        return None
    return competitor.price + (competitor.shipping_cost or Decimal("0"))


def _landed_sort_key(candidate: MatchCandidate) -> tuple[bool, Decimal]:
    """Sort key placing priced offers (cheapest first) before unpriced ones."""
    landed = _landed(candidate)
    return (landed is None, landed if landed is not None else Decimal("0"))


def _pdp_verified_label(candidate: MatchCandidate) -> str:
    """``yes`` / ``no`` when PDP ran for this candidate, else blank."""
    verified = candidate.match_details.get("pdp_verified")
    if verified is None:
        return ""
    return "yes" if verified else "no"


def _shipping_note(shipping_cost: Decimal | None) -> str:
    """A human note for the shipping column: ``free`` / ``unknown`` / blank."""
    if shipping_cost is None:
        return "unknown"
    return "free" if shipping_cost == 0 else ""


class LandscapeCsvWriter:
    """Serialize the full competitor price landscape (raw rows + summary)."""

    def write(self, results: list[MatchResult], output_path: Path) -> dict[str, object]:
        """Write raw rows to ``output_path`` and a ``*_summary.csv`` + ``*_stats.csv``.

        Returns:
            A summary stats dict (also written to the stats sidecar).
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        raw_rows: list[dict[str, str]] = []
        summary_rows: list[dict[str, str]] = []
        for result in results:
            raw_rows.extend(self._raw_rows(result))
            summary_rows.append(self._summary_row(result))

        pl.DataFrame(raw_rows, schema=list(RAW_COLUMNS), orient="row").write_csv(output_path)
        summary_path = output_path.with_name(f"{output_path.stem}_summary.csv")
        pl.DataFrame(summary_rows, schema=list(SUMMARY_COLUMNS), orient="row").write_csv(
            summary_path
        )

        stats = self.compute_stats(results)
        self._write_stats(stats, output_path)
        return stats

    def _raw_rows(self, result: MatchResult) -> list[dict[str, str]]:
        """One row per confirmed offer (cheapest landed first); a placeholder if none."""
        base = self._catalog_base(result.catalog_product)
        if not result.all_candidates:
            row = dict.fromkeys(RAW_COLUMNS, "")
            row.update(base)
            return [row]

        rows: list[dict[str, str]] = []
        for candidate in sorted(result.all_candidates, key=_landed_sort_key):
            competitor = candidate.competitor_product
            landed = _landed(candidate)
            row = dict(base)
            row.update(
                {
                    "competitor_name": competitor.seller or competitor.source,
                    "competitor_domain": _domain(competitor.url),
                    "competitor_url": competitor.url,
                    "competitor_price": "" if competitor.price is None else _q(competitor.price),
                    "competitor_shipping": (
                        "" if competitor.shipping_cost is None else _q(competitor.shipping_cost)
                    ),
                    "competitor_landed": "" if landed is None else _q(landed),
                    "confidence": confidence_tier_for(candidate),
                    "match_field": match_field_for(candidate),
                    "in_stock": _IN_STOCK_LABELS[competitor.availability],
                    "source": competitor.source,
                    "scraped_at": competitor.scraped_at or "",
                    "confirmation_method": confirmation_method_for(candidate),
                    "pdp_verified": _pdp_verified_label(candidate),
                    "source_provider": competitor.source_provider or "",
                    "extraction_method": str(
                        candidate.match_details.get("extraction_method") or ""
                    ),
                    "shipping_note": _shipping_note(competitor.shipping_cost),
                }
            )
            rows.append(row)
        return rows

    @staticmethod
    def _catalog_base(catalog: CatalogProduct) -> dict[str, str]:
        """The per-product (left-hand) columns shared by every offer row."""
        return {
            "product_sku": catalog.sku,
            "product_ean": catalog.ean or "",
            "product_name": catalog.title,
            "product_brand": catalog.brand,
            "our_price": "" if catalog.price is None else _q(catalog.price),
            "our_cogs": "" if catalog.cogs is None else _q(catalog.cogs),
            "currency": catalog.currency or "EUR",
        }

    def _summary_row(self, result: MatchResult) -> dict[str, str]:
        """Per-product summary derived from its confirmed offers."""
        catalog = result.catalog_product
        row = dict.fromkeys(SUMMARY_COLUMNS, "")
        row.update(
            {
                "product_sku": catalog.sku,
                "product_name": catalog.title,
                "product_brand": catalog.brand,
                "our_price": "" if catalog.price is None else _q(catalog.price),
                "currency": catalog.currency or "EUR",
                "n_competitor": str(len(result.all_candidates)),
            }
        )
        if not result.all_candidates:
            return row

        # Only priced offers contribute to the landed-price aggregates.
        priced = sorted(
            (c for c in result.all_candidates if _landed(c) is not None),
            key=_landed_sort_key,
        )
        if not priced:
            return row
        landeds = [landed for c in priced if (landed := _landed(c)) is not None]
        cheapest = priced[0].competitor_product
        min_landed = landeds[0]
        row.update(
            {
                "min_landed": _q(min_landed),
                "median_landed": _q(median(landeds)),
                "max_landed": _q(landeds[-1]),
                "cheapest_competitor": cheapest.seller or cheapest.source,
                "cheapest_url": cheapest.url,
            }
        )
        if catalog.price is not None:
            cheaper = sum(1 for landed in landeds if landed < catalog.price)
            row["position"] = str(cheaper + 1)
        # Suggest the cheapest landed price, never below our cost of goods.
        suggested = min_landed
        if catalog.cogs is not None and suggested < catalog.cogs:
            suggested = catalog.cogs
        row["suggested_price"] = _q(suggested)
        return row

    @staticmethod
    def compute_stats(results: list[MatchResult]) -> dict[str, object]:
        """Product-level stats (shared with :class:`CsvWriter`) plus offer counts."""
        stats = CsvWriter.compute_stats(results)
        candidates = [c for r in results for c in r.all_candidates]
        total_offers = len(candidates)
        matched = stats["matched_products"]
        stats["total_offers"] = total_offers
        stats["avg_offers_per_matched"] = round(total_offers / matched, 2) if matched else 0.0
        stats["offers_with_shipping"] = sum(
            1 for c in candidates if c.competitor_product.shipping_cost is not None
        )
        stats["offers_pdp_verified"] = sum(
            1 for c in candidates if c.match_details.get("pdp_verified")
        )
        return stats

    @staticmethod
    def _write_stats(stats: dict[str, object], output_path: Path) -> None:
        """Write the stats as a flat key/value CSV next to the results file."""
        stats_path = output_path.with_name(f"{output_path.stem}_stats.csv")
        rows = [
            {"metric": key, "value": json.dumps(value) if isinstance(value, dict) else str(value)}
            for key, value in stats.items()
        ]
        pl.DataFrame(rows, schema=["metric", "value"], orient="row").write_csv(stats_path)
