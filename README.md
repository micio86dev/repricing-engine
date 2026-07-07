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

The repo ships a ready-to-run SearXNG (`docker-compose.yml` + `searxng/settings.yml`, with the JSON
API enabled and the localhost limiter off), and `.env.example` already points at it:

```bash
docker compose up -d          # SearXNG JSON API on http://localhost:8888
# .env: SEARXNG_BASE_URL=http://localhost:8888  (already set from .env.example)
```

SearXNG aggregates many engines (Google/Bing/Brave/Qwant/DuckDuckGo/…) server-side, so it reaches
retailers a plain scraper can't. If `SEARXNG_BASE_URL` is empty, SearXNG is skipped with a one-line
hint and the other providers still run; with no provider available, `--fetch` degrades gracefully to
the CSV-only result.

> **Rate limits.** The upstream engines throttle by IP. On very large or rapidly-repeated runs they
> may temporarily return CAPTCHAs and offer counts drop. The engine paces itself
> (`SEARXNG_MAX_CONCURRENCY`, `SEARXNG_RATE_LIMIT_SECONDS`, `SEARXNG_MAX_PAGES`); on a constrained IP
> a gentle profile (`SEARXNG_MAX_CONCURRENCY=1 SEARXNG_RATE_LIMIT_SECONDS=2 SEARXNG_MAX_PAGES=1`)
> spreads the available capacity across more products. Full volume returns once the IP cools down.

#### Data sources at a glance

Discovery providers run concurrently (cheapest-first) then deduplicate; every discovered offer's
product page is then read for the real price/shipping/stock.

**Free — used out of the box**

| Source | What it gives | Notes |
|---|---|---|
| **SearXNG** (self-hosted) | competitor URLs | Primary discovery; aggregates Google/Bing/Brave/Qwant/… server-side. Free via `docker compose up -d`. |
| **eBay Browse API** | offers with **price + shipping + quantity** natively | Official, free tier (~5k calls/day). Set `EBAY_ENABLED=true` + a Production keyset. |
| **TrovaPrezzi** | IT comparison offers (price + shipping) | IT-only; **DataDome-protected** — server-side scraping is blocked (403/404 + a `datadome` cookie). Reach it via a partner **merchant feed** (`FEED_URLS`), not scraping. |
| **DuckDuckGo** | competitor URLs | HTML-endpoint SERP fallback; per-IP rate-limited. |
| **CSV file** | your existing competitor export | The `--competitors` OxyLabs/CSV pool. |
| **Groq (Llama)** | AI match-gate + last-resort PDP extraction | Free tier; skip with `--skip-ai`. |

**Paid — opt-in** (credentials wired in `config.py`/`.env.example`; adapters added per service — see [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md))

| Source | Layer | Rough cost |
|---|---|---|
| **DataForSEO** (Google Shopping/SERP) | discovery + priced offers + GTIN | ~$0.6–1 / 1k |
| **Serper.dev** | Google SERP/Shopping | ~$0.3–1 / 1k (2.5k free) |
| **SearchAPI.io / SerpApi** | Google SERP/Shopping | ~$4 / ~$3.75 per 1k |
| **OxyLabs** | SERP / e-commerce scraper | ~$0.8–2.8 / 1k |
| **ScraperAPI** | PDP proxy/renderer | credit-based |
| **Keepa** | Amazon price/history | ~€20–50/mo |
| **Affiliate feeds** (Awin, CJ, Rakuten, TradeDoubler, Impact; Idealo/TrovaPrezzi merchant) | structured product feeds | free once approved (`FEED_URLS`) |

**Read via Playwright scraping — PDP verification**

Any discovered offer URL (from any source above) is opened to read its real price, shipping and
stock and confirm the product identifier. Fetching is `httpx` first, falling back to **Playwright**
for JS-heavy / Cloudflare-protected shops (`uv sync --extra playwright && uv run playwright install
chromium`, then `PDP_PLAYWRIGHT_ENABLED=true`). Per-page extraction order, cheapest first:
JSON-LD/schema.org → meta tags → visible-text regex → Groq AI (last resort).

#### Scaling with paid providers

For thousands of products/day the free engines aren't enough. **[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)**
is a full registration guide for paid discovery/shopping/scraping APIs (DataForSEO, Serper.dev,
Oxylabs, ScraperAPI, eBay, Keepa, RSS/feeds) — with pricing, step-by-step signup, the `.env`
variable each maps to, and a `curl` test for every key. Credentials are already wired into
`config.py`/`.env.example`; provider adapters are added per chosen service.

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

### Usage modes

The **default** is the full online precise pipeline: discover competitors (`--fetch`), read each
offer's real price + shipping, keep only proven-same-product offers (`--strict-match`), and drop any
offer without a known price **and** shipping cost (`--require-shipping`). Loosen with the opt-outs.

```bash
# 1) Default — many precise, complete (price+shipping) competitors per product
uv run repricing match --catalog catalog.csv --output output/results.csv

# 2) Offline CSV-only (legacy classic behavior)
uv run repricing match --catalog catalog.csv --competitors oxylabs.csv --no-fetch --no-require-shipping

# 3) Also force full page verification of every candidate
uv run repricing match --catalog catalog.csv --verify-pdp

# 4) Keep price-less / title-similarity offers too
uv run repricing match --catalog catalog.csv --no-require-shipping --no-strict-match
```

#### Maximum coverage, 100% correct matches (recommended for `catalog.csv` → `output/results.csv`)

Find **as many real offers as possible, from every available source, for every product** — and keep
**only offers proven to be the exact same product** (EAN/GTIN, a confirmed SKU, or the identifier
found on the product page); title-similarity guesses are dropped.

This is now the **default** behaviour — the command below is explicit for clarity, but `--fetch`,
`--strict-match`, `--require-shipping` and `--max-pdp-per-product 0` are all on by default:

```bash
# Make sure SearXNG is up first:  docker compose up -d
uv run repricing match \
  --catalog catalog.csv \
  --verify-pdp \
  --output output/results.csv
```

- `--fetch` *(default on)* — discover competitors across all providers (SearXNG's many engines +
  DuckDuckGo + TrovaPrezzi + eBay) and read each offer's real price/shipping/stock.
- `--verify-pdp` — visit every product page to also confirm the identifier (beyond the price/shipping
  read `--fetch` already does).
- `--strict-match` *(default on)* — keep **only** identifier-confirmed offers (`match_field` =
  `sku`/`gtin`, or a `PDP·…` confirmation); drop `snippet` (title-only) matches.
- `--require-shipping` *(default on)* — keep only offers with a known price **and** shipping cost.
- `--max-pdp-per-product 0` *(default)* — **no limit**: price and verify *every* discovered offer.

> On a rate-limited IP, prefix the throttle-safe profile from the note above, e.g.
> `SEARXNG_MAX_CONCURRENCY=1 SEARXNG_RATE_LIMIT_SECONDS=2 SEARXNG_MAX_PAGES=1 uv run repricing match …`.

`--max-pdp-per-product` is **0 (no cap) by default** — every discovered offer's page is read; set a
positive number to trade completeness for speed.
The optional Playwright JS-rendering fallback (for Cloudflare/JS-heavy shops):
`uv sync --extra playwright && uv run playwright install chromium`, then set `PDP_PLAYWRIGHT_ENABLED=true`.

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
│   ├── enrichment.py   # stamp catalog SKU/EAN/brand when confirmed in a result
│   ├── snippet_price.py# best-effort price from a SERP snippet
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
