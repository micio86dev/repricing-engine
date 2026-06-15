# Repricing Engine

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

See all options with `uv run repricing match --help`.

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
│   └── layers/         # exact_id, sku_brand, semantic, ai_quality_gate
├── output/             # CSV writer
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
