"""End-to-end test of the enhanced pipeline: fetch -> match -> verify -> CSV.

Fully offline: the source providers and PDP fetcher share an ``httpx.MockTransport``
(SearXNG JSON + a JSON-LD product page from fixtures), the sentence encoder is the
deterministic :class:`FakeEncoder`, and the AI gate is skipped. Asserts the new
multi-source / PDP columns are populated and that matching still succeeds.
"""

from pathlib import Path

import httpx
import polars as pl

from repricing_engine.config import Settings
from repricing_engine.matching.pipeline import MatchingPipeline
from repricing_engine.models.enums import Market
from repricing_engine.models.product import CatalogProduct
from repricing_engine.orchestration.enhanced_pipeline import EnhancedPipeline
from repricing_engine.output.landscape_writer import RAW_COLUMNS, LandscapeCsvWriter
from tests.conftest import FakeEncoder


def _settings() -> Settings:
    return Settings(
        groq_api_key=None,
        searxng_base_url="http://localhost:8888",
        trovaprezzi_enabled=False,
        duckduckgo_enabled=False,
        pdp_rate_limit_per_domain_seconds=0.0,
        log_level="WARNING",
    )


def _catalog() -> CatalogProduct:
    return CatalogProduct(
        sku="APL-IPH13-128",
        ean="4006381333931",
        gtin="4006381333931",
        brand="Apple",
        title="Apple iPhone 13 128GB Blue",
        category="Smartphones",
        market=Market.IT,
    )


def _handler(searxng_json: str, pdp_html: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/search" in request.url.path:
            return httpx.Response(
                200, text=searxng_json, headers={"content-type": "application/json"}
            )
        return httpx.Response(200, text=pdp_html)  # any product page -> JSON-LD fixture

    return handler


async def test_fetch_match_verify_to_csv(tmp_path: Path, fixture_text, mock_async_client):
    settings = _settings()
    pipeline = MatchingPipeline(settings, skip_ai=True, min_confidence=0.6, encoder=FakeEncoder())
    client = mock_async_client(
        _handler(fixture_text("searxng_response.json"), fixture_text("pdp_with_jsonld.html"))
    )

    async with client:
        enhanced = EnhancedPipeline.build(
            settings, client, matching_pipeline=pipeline, fetch=True, verify_pdp=True
        )
        results = await enhanced.run(
            [_catalog()],
            csv_pool=[],
            market=Market.IT,
            fetch=True,
            verify_pdp=True,
            max_pdp_per_product=15,
            show_progress=False,
        )

    # Competitors were discovered online and matched.
    assert len(results) == 1
    result = results[0]
    assert result.is_matched
    assert result.all_candidates, "expected fetched competitors to match the catalog product"

    # Every matched candidate was PDP-verified off the JSON-LD page.
    verified = result.all_candidates[0]
    assert verified.match_details["pdp_verified"] is True
    assert verified.match_details["confirmation_method"] == "PDP·GTIN"
    assert verified.competitor_product.source_provider == "searxng"

    # Write the landscape CSV and confirm the new columns are present + populated.
    out = tmp_path / "results.csv"
    stats = LandscapeCsvWriter().write(results, out)
    frame = pl.read_csv(out, infer_schema_length=0)
    assert frame.columns == list(RAW_COLUMNS)

    offer_rows = frame.filter(pl.col("competitor_url") != "")
    assert offer_rows.height >= 1
    first = offer_rows.row(0, named=True)
    assert first["pdp_verified"] == "yes"
    assert first["confirmation_method"] == "PDP·GTIN"
    assert first["source_provider"] == "searxng"
    assert first["extraction_method"] == "json_ld"
    assert first["competitor_price"] == "789.00"
    assert stats["offers_pdp_verified"] >= 1
