"""Typer CLI entrypoint for the repricing engine."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import httpx
import typer
from rich.console import Console
from rich.table import Table

from repricing_engine.config import Settings
from repricing_engine.exceptions import RepricingError
from repricing_engine.ingestion.base import read_csv_rows
from repricing_engine.ingestion.catalog_ingestor import CatalogIngestor
from repricing_engine.ingestion.oxylabs_ingestor import OxyLabsIngestor
from repricing_engine.matching.pipeline import MatchingPipeline
from repricing_engine.models.enums import Market
from repricing_engine.normalization.market import detect_market
from repricing_engine.orchestration.enhanced_pipeline import EnhancedPipeline
from repricing_engine.output.completeness_filter import keep_complete_offers
from repricing_engine.output.landscape_writer import LandscapeCsvWriter
from repricing_engine.utils.logging import setup_logging

if TYPE_CHECKING:
    from repricing_engine.models.match_result import MatchResult
    from repricing_engine.models.product import CatalogProduct, CompetitorProduct

app = typer.Typer(
    help="Intelligent repricing engine — multi-layer product matching.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.callback()
def main() -> None:
    """Intelligent repricing engine — multi-layer product matching."""


def _parse_market(value: str) -> Market:
    """Parse a market code into a :class:`Market`, exiting on an invalid value."""
    try:
        return Market(value.strip().upper())
    except ValueError as exc:
        valid = ", ".join(m.value for m in Market)
        console.print(f"[red]Invalid market '{value}'. Valid markets: {valid}[/red]")
        raise typer.Exit(code=2) from exc


@app.command()
def match(
    catalog: Annotated[
        Path,
        typer.Option(help="Path to catalog CSV", exists=True, dir_okay=False, readable=True),
    ],
    competitors: Annotated[
        Path | None,
        typer.Option(
            help="Path to OxyLabs/competitors CSV (optional when --fetch is used)",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
    output: Annotated[Path, typer.Option(help="Output path")] = Path("output/results.csv"),
    market: Annotated[
        str | None,
        typer.Option(
            help="Market code (IT, DE, FR, ES...); auto-detected from the data if omitted"
        ),
    ] = None,
    skip_ai: Annotated[bool, typer.Option("--skip-ai", help="Skip AI quality gate layer")] = False,
    min_confidence: Annotated[float, typer.Option(help="Minimum confidence threshold")] = 0.60,
    fetch: Annotated[
        bool,
        typer.Option(
            "--fetch/--no-fetch",
            help="Discover competitors online via free/cheap providers (default: on). "
            "Use --no-fetch for a CSV-only run against --competitors.",
        ),
    ] = True,
    verify_pdp: Annotated[
        bool,
        typer.Option("--verify-pdp", help="Visit product pages to confirm IDs and real prices"),
    ] = False,
    max_pdp_per_product: Annotated[
        int,
        typer.Option(
            help="Max product pages to read per catalog product (default 0 = no limit: "
            "read every discovered offer's page). Set a positive cap to trade completeness "
            "for speed."
        ),
    ] = 0,
    strict_match: Annotated[
        bool,
        typer.Option(
            "--strict-match/--no-strict-match",
            help="Keep only offers proven to be the same product (EAN/GTIN/SKU or a "
            "PDP identifier hit); drop title-similarity ('snippet') matches (default: on).",
        ),
    ] = True,
    require_shipping: Annotated[
        bool,
        typer.Option(
            "--require-shipping/--no-require-shipping",
            help="Keep only offers with a known price AND shipping cost (free/0 counts); "
            "drop offers whose landed cost is uncertain (default: on). "
            "Use --no-require-shipping to keep price-less/shipping-less offers.",
        ),
    ] = True,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging")] = False,
) -> None:
    """Match a catalog against competitors and write a results CSV.

    By default this runs the full online pipeline: it discovers competitors via
    the configured providers (--fetch), reads each offer's real price and shipping
    from its product page, keeps only offers proven to be the same product
    (--strict-match), and drops any offer without a known price AND shipping cost
    (--require-shipping). Stock quantity/status is captured when available but never
    required. Use --no-fetch / --no-strict-match / --no-require-shipping to loosen,
    e.g. `--no-fetch --competitors file.csv` for an offline CSV-only run.
    """
    settings = Settings()
    setup_logging("DEBUG" if verbose else settings.log_level)

    try:
        market_enum = _resolve_market(market, catalog, competitors)
        catalog_products = CatalogIngestor(default_market=market_enum).ingest(catalog)
        csv_pool = (
            OxyLabsIngestor(default_market=market_enum).ingest(competitors) if competitors else []
        )
        if not csv_pool and not fetch:
            console.print(
                "[yellow]No competitors CSV and --fetch not set: there is nothing to match "
                "against.[/yellow]"
            )

        console.print(
            f"Loaded [bold]{len(catalog_products)}[/bold] catalog products and "
            f"[bold]{len(csv_pool)}[/bold] competitor products (market {market_enum})."
        )
        if fetch or verify_pdp:
            console.print(
                f"Online mode: fetch=[bold]{fetch}[/bold], verify-pdp=[bold]{verify_pdp}[/bold]."
            )

        pipeline = MatchingPipeline(settings, skip_ai=skip_ai, min_confidence=min_confidence)
        if fetch or verify_pdp:
            results = asyncio.run(
                _run_enhanced(
                    settings,
                    pipeline,
                    catalog_products,
                    csv_pool,
                    market_enum,
                    fetch=fetch,
                    verify_pdp=verify_pdp,
                    max_pdp_per_product=max_pdp_per_product,
                    strict_match=strict_match,
                )
            )
        else:
            results = pipeline.run(catalog_products, csv_pool)
        if require_shipping:
            if not (fetch or verify_pdp):
                console.print(
                    "[yellow]--require-shipping without --fetch/--verify-pdp: shipping is read "
                    "from product pages, so offers lacking a CSV shipping value will be "
                    "dropped and many products may end up with no offers.[/yellow]"
                )
            results = keep_complete_offers(results)
        stats = LandscapeCsvWriter().write(results, output)
    except RepricingError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    _print_summary(stats, output)


def _resolve_market(market: str | None, catalog: Path, competitors: Path | None) -> Market:
    """Resolve the market from ``--market`` or auto-detect it from the data."""
    if market is not None:
        return _parse_market(market)
    competitor_rows = read_csv_rows(competitors) if competitors else None
    detected = detect_market(read_csv_rows(catalog), competitor_rows)
    console.print(f"Auto-detected market: [bold]{detected}[/bold]")
    return detected


async def _run_enhanced(
    settings: Settings,
    pipeline: MatchingPipeline,
    catalog_products: list[CatalogProduct],
    csv_pool: list[CompetitorProduct],
    market: Market,
    *,
    fetch: bool,
    verify_pdp: bool,
    max_pdp_per_product: int,
    strict_match: bool = False,
) -> list[MatchResult]:
    """Run the async fetch/verify pipeline within a shared HTTP client lifecycle."""
    async with httpx.AsyncClient() as client:
        enhanced = EnhancedPipeline.build(
            settings,
            client,
            matching_pipeline=pipeline,
            fetch=fetch,
            verify_pdp=verify_pdp,
        )
        return await enhanced.run(
            catalog_products,
            csv_pool=csv_pool,
            market=market,
            fetch=fetch,
            verify_pdp=verify_pdp,
            max_pdp_per_product=max_pdp_per_product,
            strict_match=strict_match,
        )


def _print_summary(stats: dict[str, object], output: Path) -> None:
    """Render the run summary as a Rich table."""
    table = Table(title="Repricing — Match Summary")
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")

    table.add_row("Total products", str(stats.get("total_products")))
    table.add_row("Matched products", str(stats.get("matched_products")))
    table.add_row("Match rate", f"{stats.get('match_rate_pct')}%")
    table.add_row("Total competitor offers", str(stats.get("total_offers")))
    table.add_row("Avg offers / matched", str(stats.get("avg_offers_per_matched")))
    table.add_row("Offers with shipping", str(stats.get("offers_with_shipping")))
    table.add_row("PDP-verified offers", str(stats.get("offers_pdp_verified")))
    table.add_row("Confidence distribution", str(stats.get("confidence_distribution")))
    table.add_row("Match method distribution", str(stats.get("match_method_distribution")))

    console.print(table)
    summary_path = output.with_name(f"{output.stem}_summary.csv")
    console.print(f"Offer rows written to [bold]{output}[/bold]")
    console.print(f"Per-product summary written to [bold]{summary_path}[/bold]")


if __name__ == "__main__":
    app()
