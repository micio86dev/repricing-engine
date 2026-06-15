"""Typer CLI entrypoint for the repricing engine."""

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from repricing_engine.config import Settings
from repricing_engine.exceptions import RepricingError
from repricing_engine.ingestion.catalog_ingestor import CatalogIngestor
from repricing_engine.ingestion.oxylabs_ingestor import OxyLabsIngestor
from repricing_engine.matching.pipeline import MatchingPipeline
from repricing_engine.models.enums import Market
from repricing_engine.output.csv_writer import CsvWriter
from repricing_engine.utils.logging import setup_logging

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
        Path,
        typer.Option(
            help="Path to OxyLabs/competitors CSV",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ],
    output: Annotated[Path, typer.Option(help="Output path")] = Path("output/results.csv"),
    market: Annotated[str, typer.Option(help="Market code (IT, DE, FR, ES...)")] = "IT",
    skip_ai: Annotated[bool, typer.Option("--skip-ai", help="Skip AI quality gate layer")] = False,
    min_confidence: Annotated[float, typer.Option(help="Minimum confidence threshold")] = 0.60,
    verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Verbose logging")] = False,
) -> None:
    """Match a catalog against competitor products and write a results CSV."""
    settings = Settings()
    setup_logging("DEBUG" if verbose else settings.log_level)
    market_enum = _parse_market(market)

    try:
        catalog_products = CatalogIngestor(default_market=market_enum).ingest(catalog)
        competitor_products = OxyLabsIngestor(default_market=market_enum).ingest(competitors)

        console.print(
            f"Loaded [bold]{len(catalog_products)}[/bold] catalog products and "
            f"[bold]{len(competitor_products)}[/bold] competitor products "
            f"(market {market_enum})."
        )

        pipeline = MatchingPipeline(
            settings,
            skip_ai=skip_ai,
            min_confidence=min_confidence,
        )
        results = pipeline.run(catalog_products, competitor_products)
        stats = CsvWriter().write(results, output)
    except RepricingError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    _print_summary(stats, output)


def _print_summary(stats: dict[str, object], output: Path) -> None:
    """Render the run summary as a Rich table."""
    table = Table(title="Repricing — Match Summary")
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")

    table.add_row("Total products", str(stats.get("total_products")))
    table.add_row("Matched products", str(stats.get("matched_products")))
    table.add_row("Match rate", f"{stats.get('match_rate_pct')}%")
    table.add_row("Confidence distribution", str(stats.get("confidence_distribution")))
    table.add_row("Match method distribution", str(stats.get("match_method_distribution")))

    console.print(table)
    console.print(f"Results written to [bold]{output}[/bold]")


if __name__ == "__main__":
    app()
