"""Write match results (and summary stats) to CSV."""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING

import polars as pl

from repricing_engine.matching.scoring import confidence_to_level

if TYPE_CHECKING:
    from pathlib import Path

    from repricing_engine.models.match_result import MatchResult

# Output columns, in order.
RESULT_COLUMNS: tuple[str, ...] = (
    "catalog_sku",
    "catalog_ean",
    "catalog_title",
    "catalog_brand",
    "catalog_market",
    "matched_competitor_title",
    "matched_competitor_brand",
    "matched_price",
    "matched_currency",
    "matched_shipping",
    "matched_source",
    "matched_url",
    "confidence",
    "confidence_level",
    "match_method",
    "match_details",
    "all_candidates_count",
    "rejected_count",
)


class CsvWriter:
    """Serialize :class:`MatchResult`s to a results CSV plus a stats sidecar."""

    def write(self, results: list[MatchResult], output_path: Path) -> dict[str, object]:
        """Write results to ``output_path`` and a ``*_stats.csv`` sidecar.

        Args:
            results: The per-product match results.
            output_path: Destination CSV path (parent dirs are created).

        Returns:
            A summary stats dict (also written to the sidecar file).
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        rows = [self._row(result) for result in results]
        frame = pl.DataFrame(rows, schema=list(RESULT_COLUMNS), orient="row")
        frame.write_csv(output_path)

        stats = self.compute_stats(results)
        self._write_stats(stats, output_path)
        return stats

    @staticmethod
    def _row(result: MatchResult) -> dict[str, str]:
        """Flatten a single result into the output row schema."""
        catalog = result.catalog_product
        row: dict[str, str] = {
            "catalog_sku": catalog.sku,
            "catalog_ean": catalog.ean or "",
            "catalog_title": catalog.title,
            "catalog_brand": catalog.brand,
            "catalog_market": str(catalog.market),
            "matched_competitor_title": "",
            "matched_competitor_brand": "",
            "matched_price": "",
            "matched_currency": "",
            "matched_shipping": "",
            "matched_source": "",
            "matched_url": "",
            "confidence": "",
            "confidence_level": "",
            "match_method": "",
            "match_details": "",
            "all_candidates_count": str(len(result.all_candidates)),
            "rejected_count": str(len(result.rejected_candidates)),
        }

        best = result.best_match
        if best is not None:
            competitor = best.competitor_product
            row.update(
                {
                    "matched_competitor_title": competitor.title,
                    "matched_competitor_brand": competitor.brand or "",
                    "matched_price": str(competitor.price),
                    "matched_currency": competitor.currency,
                    "matched_shipping": (
                        "" if competitor.shipping_cost is None else str(competitor.shipping_cost)
                    ),
                    "matched_source": competitor.source,
                    "matched_url": competitor.url,
                    "confidence": f"{best.confidence:.4f}",
                    "confidence_level": str(confidence_to_level(best.confidence)),
                    "match_method": str(best.match_method),
                    "match_details": json.dumps(best.match_details, default=str),
                }
            )
        return row

    @staticmethod
    def compute_stats(results: list[MatchResult]) -> dict[str, object]:
        """Compute summary statistics over all results."""
        total = len(results)
        matched = [r for r in results if r.is_matched]
        match_methods: Counter[str] = Counter()
        confidence_levels: Counter[str] = Counter()
        for result in matched:
            best = result.best_match
            if best is None:
                continue
            match_methods[str(best.match_method)] += 1
            confidence_levels[str(confidence_to_level(best.confidence))] += 1

        matched_count = len(matched)
        return {
            "total_products": total,
            "matched_products": matched_count,
            "match_rate_pct": round((matched_count / total * 100) if total else 0.0, 2),
            "confidence_distribution": dict(confidence_levels),
            "match_method_distribution": dict(match_methods),
        }

    @staticmethod
    def _write_stats(stats: dict[str, object], output_path: Path) -> None:
        """Write the stats as a flat key/value CSV next to the results file."""
        stats_path = output_path.with_name(f"{output_path.stem}_stats.csv")
        rows = [
            {"metric": key, "value": json.dumps(value) if isinstance(value, dict) else str(value)}
            for key, value in stats.items()
        ]
        pl.DataFrame(rows, schema=["metric", "value"], orient="row").write_csv(stats_path)
