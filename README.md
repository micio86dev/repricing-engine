# Repricing Engine

[![CI](https://github.com/micio86dev/repricing-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/micio86dev/repricing-engine/actions/workflows/ci.yml)

> Intelligent repricing / price-monitoring engine for e-commerce — multi-layer product matching with an AI quality gate.

A CLI MVP that takes a product catalog and a competitor (OxyLabs) export, runs a four-layer
matching cascade with confidence scoring, and produces a results CSV. Built to prove **superior
product-matching accuracy** versus off-the-shelf tools.

## Problem Statement

E-commerce operators that monitor competitor pricing across markets (IT, DE, FR, ES, …) are let
down by existing tools (PriceShape, OxyLabs) at the most important step — **matching the right
products**:

- **EAN/GTIN lookups return garbage.** Identifier databases are full of duplicates and errors, so a
  raw EAN match silently pairs unrelated products.
- **SKU searches miss products.** Competitor SKUs differ from yours; exact lookups fail.
- **Shipping costs are ignored.** A "cheaper" competitor is often more expensive once delivery is
  included.

The core insight: **never trust a single identifier.** This engine cross-validates every match
across multiple signals and uses an LLM as the final arbiter for ambiguous cases.

## Architecture

```
 catalog.csv ─► CatalogIngestor  ─►  [CatalogProduct]
                                            │
 oxylabs.csv ─► OxyLabsIngestor  ─►  [CompetitorProduct]
                                            │
                                            ▼
                                   MatchingPipeline
                                            │
   ┌────────────────────────────────────────────────────────────────┐
   │  Layer 1: Exact ID  (EAN/GTIN)            → confidence 0.95+     │
   │     ↓ always continues to cross-validate brand + category       │
   │  Layer 2: SKU + Brand (rapidfuzz)         → confidence 0.80+     │
   │     ↓ if no high-confidence match yet                           │
   │  Layer 3: Semantic  (embeddings, cosine)  → confidence 0.60+     │
   │     ↓ all candidates above threshold                           │
   │  Layer 4: AI Quality Gate (Groq / Llama)  → confirm / reject     │
   └────────────────────────────────────────────────────────────────┘
                                            │
                                            ▼
                                     [MatchResult]
                                            │
                                            ▼
                                CsvWriter ─► output.csv
```

Each layer emits `MatchCandidate`s with a confidence score and an explanation. The pipeline keeps
all candidates (matched and rejected) so results are auditable.

### Finding more competitors — multi-source fetching & PDP verification (opt-in)

A single static CSV usually surfaces too few competitors. Two **opt-in** stages wrap the matcher
(both off by default — the CSV-only flow above is unchanged):

- **`--fetch` — source fetching.** Discover competitor URLs via free providers run concurrently
  then deduplicated: **SearXNG** (primary, self-hosted metasearch), **TrovaPrezzi** (IT-only
  comparison pages), **DuckDuckGo** (HTML-endpoint fallback), and a **CSV file** provider.
- **`--verify-pdp` — page verification.** Visit each candidate's product page and confirm the
  EAN/GTIN/SKU is really on it, reading the *real* price, shipping, and stock. A cheap→expensive
  cascade keeps cost down: **regex + JSON-LD (free)** first, **Groq AI extraction** only as a last
  resort. This is what turns the `PDP·GTIN` / `PDP·SKU` confidence labels from heuristics into facts.

```
 catalog ─► csv_pool ─┐
                      ├─(--fetch)─► SourceFetcher ─► more competitors
                      ▼
        EnhancedPipeline ─ per product ─► MatchingPipeline (Layer 1→4)
                      ▼
        (--verify-pdp) PdpVerifier:  fetch ─► IDs/JSON-LD (free) ─► AI (last resort)
                      ▼
                 LandscapeCsvWriter ─► output.csv  (+ _summary, + _stats)
```

#### SearXNG quick start (free, self-hosted)

```bash
docker run --rm -d -p 8888:8080 searxng/searxng
# then in .env:  SEARXNG_BASE_URL=http://localhost:8888
```

If `SEARXNG_BASE_URL` is empty, SearXNG is skipped with a one-line hint and the other providers
still run. With no provider available, `--fetch` degrades gracefully to the CSV-only result.

## Quick Start

### Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** package manager
- (optional) A **Groq API key** for the AI quality gate — get one at <https://console.groq.com>

### Installation

```bash
uv sync
```

The first run that uses the semantic layer downloads the embedding model
(`all-MiniLM-L6-v2`, ~90 MB) to `~/.cache/huggingface/`.

### Environment

```bash
cp .env.example .env
# edit .env — set GROQ_API_KEY and adjust thresholds if needed
```

All thresholds and model names are configurable via environment variables (see `.env.example`).

### Run

```bash
uv run repricing match \
  --catalog tests/fixtures/sample_catalog.csv \
  --competitors tests/fixtures/sample_oxylabs_export.csv \
  --output output/results.csv \
  --market IT
```

Skip the LLM layer (no API key required) with `--skip-ai`:

```bash
uv run repricing match \
  --catalog tests/fixtures/sample_catalog.csv \
  --competitors tests/fixtures/sample_oxylabs_export.csv \
  --skip-ai
```

### Four usage modes

```bash
# 1) CSV-only (default, fully offline) — unchanged classic behavior
uv run repricing match --catalog catalog.csv --competitors oxylabs.csv

# 2) CSV + discover more competitors online
uv run repricing match --catalog catalog.csv --competitors oxylabs.csv --fetch

# 3) CSV + verify each candidate's product page (real price/shipping/stock)
uv run repricing match --catalog catalog.csv --competitors oxylabs.csv --verify-pdp

# 4) Online-only — no CSV; discover and verify everything
uv run repricing match --catalog catalog.csv --fetch --verify-pdp
```

`--max-pdp-per-product` (default 15) caps how many pages are verified per product. The optional
Playwright JS-rendering fallback: `uv sync --extra playwright && uv run playwright install chromium`,
then set `PDP_PLAYWRIGHT_ENABLED=true`.

See all options with `uv run repricing match --help`.

### Output

`output/results.csv` has one row per confirmed offer; `output/results_summary.csv` aggregates per
product (competitor count, min/median/max landed price, our position, suggested price floored at
COGS); `output/results_stats.csv` holds run-level stats. Alongside the existing offer columns, the
schema carries the verification fields (blank in CSV-only mode): `confirmation_method`,
`pdp_verified`, `source_provider`, `extraction_method`, `shipping_note`.

## Testing

```bash
uv run pytest                 # all tests with coverage
uv run pytest tests/unit/     # unit tests only
uv run pytest -k exact_id     # a specific pattern
uv run ruff check .           # lint
uv run ruff format --check .  # formatting check
```

Tests run fully offline: the embedding encoder and Groq client are dependency-injected and stubbed
in unit tests. Tests that exercise the real model are marked `integration`.

## Project Structure

```
src/repricing_engine/
├── cli.py              # Typer CLI entrypoint (`repricing match`)
├── config.py           # Pydantic settings (env-driven)
├── exceptions.py       # Custom exception hierarchy
├── models/             # Pydantic data contracts + enums
├── ingestion/          # CSV ingestors (catalog, OxyLabs)
├── normalization/      # text / identifiers / price normalization
├── matching/
│   ├── pipeline.py     # cascade orchestrator
│   ├── scoring.py      # confidence → level
│   ├── classification.py  # match-tier / confirmation labels
│   └── layers/         # exact_id, sku_brand, semantic, ai_quality_gate
├── sources/            # (--fetch) source fetching
│   ├── orchestrator.py # SourceFetcher (concurrent + dedupe)
│   ├── deduplicator.py # URL/domain dedup
│   ├── mapper.py       # RawSearchResult → CompetitorProduct
│   └── providers/      # searxng, trovaprezzi, duckduckgo, csv_file
├── pdp/                # (--verify-pdp) page verification
│   ├── verifier.py     # cheap→expensive cascade
│   ├── fetcher.py      # httpx → optional Playwright
│   ├── identifier_finder.py  # regex + meta + JSON-LD (free)
│   └── extractor.py    # Groq AI extraction (last resort)
├── orchestration/      # EnhancedPipeline (async bridge)
├── output/             # landscape CSV writer
└── utils/              # logging
tests/                  # unit + integration + fixtures
```

## Matching Layers & Confidence

| Layer | Method | Signal | Typical confidence |
|-------|--------|--------|--------------------|
| 1 | Exact ID | EAN / GTIN match (brand cross-checked) | 0.95 (0.70 if brand conflicts) |
| 2 | SKU + Brand | `rapidfuzz` token-sort ratio × brand boost | 0.80+ |
| 3 | Semantic | embedding cosine similarity of `brand title category` | 0.60+ |
| 4 | AI Quality Gate | Groq/Llama verdict, adjusts confidence | confirm / reject |

Confidence levels: **HIGH** (> 0.90) · **MEDIUM** (0.70–0.90) · **LOW** (< 0.70).

## Contributing

- **Git Flow.** Feature branches `feature/xxx`, fixes `fix/xxx`, off `develop`.
- **Conventional Commits.** `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`.
- **English** for all code, comments, docstrings, commit messages, and branch names.
- **TDD** — failing test first, then implementation. Keep coverage ≥ 80%.
- Run `uv run pre-commit run --all-files` before pushing.

## License

UNLICENSED — private project for Gotour Agency.
