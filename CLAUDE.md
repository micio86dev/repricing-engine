# CLAUDE.md — Repricing Engine

## Project Overview

Intelligent repricing/price monitoring engine for e-commerce. Multi-layer product matching with AI quality gate. Python 3.12+, CLI-based MVP.

## Commands

- `uv sync` — Install all dependencies
- `uv run repricing match --catalog <file> --competitors <file> --output <file>` — Run matching (CSV-only)
- `uv run repricing match --catalog <file> --fetch --verify-pdp` — Discover competitors online + verify pages
- `uv sync --extra playwright && uv run playwright install chromium` — Optional JS-rendering fallback
- `uv run pytest` — Run all tests
- `uv run pytest tests/unit/` — Run unit tests only
- `uv run pytest -k "test_exact_id"` — Run specific test pattern
- `uv run ruff check .` — Lint
- `uv run ruff format .` — Format
- `uv run pre-commit run --all-files` — Pre-commit hooks

## Architecture

### Matching Pipeline (cascade with confidence scoring)

```
Layer 1: Exact ID Match (EAN/GTIN)    → confidence 0.95+
   ↓ (always continues to cross-validate)
Layer 2: SKU + Brand Fuzzy Match       → confidence 0.80+
   ↓ (if no high-confidence match yet)
Layer 3: Semantic Match (embeddings)   → confidence 0.60+
   ↓ (all candidates above threshold)
Layer 4: AI Quality Gate (Groq/Llama)  → confirms or rejects each match
```

Key principle: **Never trust a single identifier.** Even if EAN matches, cross-validate brand + category. Layer 4 (AI) is the final arbiter for ambiguous cases.

### Source Fetching (opt-in, `--fetch`) — `src/repricing_engine/sources/`

Discovers *more* competitor offers than a static CSV holds, via free providers run
concurrently then deduplicated:

- **SearXNG** (primary, free, self-hosted metasearch — `SEARXNG_BASE_URL`)
- **TrovaPrezzi** (IT-only public price-comparison pages; bs4, AI fallback)
- **DuckDuckGo** (free HTML-endpoint SERP fallback, per-instance rate-limited)
- **CSV file** (`CsvFileProvider` wrapping `OxyLabsIngestor`)

`SourceFetcher` orders providers cheapest-first (`cost_tier`), runs the available ones
with `asyncio.gather` (each guarded so one failure can't sink the batch), maps results to
`CompetitorProduct`, and dedupes by normalized URL then one-per-domain (cheapest).

### PDP Verification (opt-in, `--verify-pdp`) — `src/repricing_engine/pdp/`

After matching, visits each candidate's product page to turn the *heuristic* `PDP·…`
labels into facts and read the real price/shipping/stock. Cheapest signal first:

```
PageFetcher (httpx → optional Playwright)  → FetchResult
   ↓
IdentifierFinder: regex(EAN/GTIN/SKU) + meta + JSON-LD   (FREE — confirms + often prices)
   ↓ (only if free pass found neither an identifier nor a price, and confidence < threshold)
AiExtractor (Groq/Llama)                                 (LAST RESORT — tokens cost money)
```

`PdpVerifier` writes the verified offer back through `CompetitorProduct.model_copy` and
annotates `MatchCandidate.match_details` (`pdp_verified`, `confirmation_method`,
`extraction_method`, `source_provider`). Fetch/parse failures degrade to the original data
with `pdp_verified=False` (zero data loss). Capped at `--max-pdp-per-product` (default 15).

### Data Flow

```
catalog.csv → CatalogIngestor → [CatalogProduct]
competitors.csv → OxyLabsIngestor → csv_pool ─┐
                                              │  (--fetch) SourceFetcher → fetched competitors
                                              ↓
EnhancedPipeline (async) ── per product ──→ MatchingPipeline.match_one (Layer 1→4, sync)
                                              ↓
                                  (--verify-pdp) PdpVerifier  → verified [MatchCandidate]
                                              ↓
                              [MatchResult] → LandscapeCsvWriter → output.csv (+_summary +_stats)
```

The default CSV-only path (no `--fetch`/`--verify-pdp`) is **byte-for-byte unchanged**:
it skips `EnhancedPipeline` and calls `MatchingPipeline.run` directly.

## Code Conventions

- **Language:** All code, comments, docstrings, commit messages, branch names in English
- **Style:** ruff enforced, line length 100, double quotes, Google-style docstrings
- **Types:** Full type annotations on all public functions. Use Pydantic models for data.
- **SOLID:** Single responsibility per module. Depend on abstractions (ABC for layers/ingestors). Open for extension (new layers, new ingestors via subclassing).
- **Testing:** TDD — write failing test first, then implement. Minimum 80% coverage.
- **Test isolation:** Tests MUST NOT touch any external/production system — no writes to a primary/production database, no live network. Write only to pytest's `tmp_path`; inject stubs for heavy deps (encoder, Groq). A session-autouse guard in `tests/conftest.py` overwrites datastore env vars (`DATABASE_URL`, …) with sandbox values so a real store can never be reached, even once a DB is added.
- **Error handling:** Never silently swallow exceptions. Use custom exception classes in `exceptions.py`.
- **Git:** Git Flow. Branches: `feature/xxx`, `fix/xxx`. Conventional Commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`.
- **No mutable global state.** Pass config via dependency injection.

## Important Patterns

### Adding a new matching layer

1. Create `src/repricing_engine/matching/layers/my_layer.py`
2. Subclass `BaseMatchLayer`
3. Implement `match(catalog_product, competitor_products) -> list[MatchCandidate]`
4. Register in `MatchingPipeline.__init__`
5. Add unit tests in `tests/unit/test_my_layer.py`

### Adding a new data source ingestor

1. Create `src/repricing_engine/ingestion/my_source_ingestor.py`
2. Subclass `BaseIngestor`
3. Implement `ingest(file_path) -> list[CompetitorProduct]`
4. Add CLI option in `cli.py`

### Adding a new source provider (`--fetch`)

1. Create `src/repricing_engine/sources/providers/my_provider.py`
2. Subclass `BaseSourceProvider`; set `name` + `cost_tier`; take an injected `httpx.AsyncClient`
3. Implement `async search(product, market) -> list[RawSearchResult]` and `is_available()`
4. Register it in `SourceFetcher.from_settings`; add a config toggle in `config.py`
5. Add unit tests injecting `httpx.MockTransport` (never the live network)

### PDP cascade rule (cheapest → most expensive)

Always try regex/meta → JSON-LD (both free) **before** the Groq `AiExtractor`. Groq tokens
cost money, so AI extraction runs only when the free pass found neither an identifier nor a
price and the candidate is still below `PDP_AI_CONFIDENCE_THRESHOLD`.

## Environment

- Python 3.12+ via uv
- `.env` file for secrets (never commit) — see `.env.example`
- All thresholds configurable via env vars
- Embedding model downloads to `~/.cache/huggingface/` on first run
- **Source fetching** (`--fetch`): `SEARXNG_BASE_URL`, `SEARXNG_ENABLED`, `TROVAPREZZI_ENABLED`,
  `DUCKDUCKGO_ENABLED`, `DUCKDUCKGO_RATE_LIMIT_SECONDS`
- **PDP verification** (`--verify-pdp`): `PDP_FETCH_TIMEOUT_SECONDS`, `PDP_MAX_CONCURRENT_FETCHES`,
  `PDP_RATE_LIMIT_PER_DOMAIN_SECONDS`, `PDP_AI_EXTRACTION_ENABLED`, `PDP_AI_CONFIDENCE_THRESHOLD`,
  `PDP_PLAYWRIGHT_ENABLED`

## Implementation Notes

- **CLI options use `Annotated[...]`** (e.g. `Annotated[Path, typer.Option(...)]`) rather than
  call-in-default, to satisfy ruff `B008`.
- **Heavy dependencies are dependency-injected**: the sentence-transformers encoder
  (`SemanticLayer`), the Groq client (`AiQualityGateLayer` / `AiExtractor`), and every
  `httpx.AsyncClient` (providers + `PageFetcher`) are passed in, so unit tests stub them with
  `FakeEncoder` / `FakeGroqClient` / `httpx.MockTransport` and avoid model downloads / network.
- **Async bridge**: fetching and PDP scraping are async; `MatchingPipeline.match_one` is sync.
  `EnhancedPipeline` (in `orchestration/`) calls the sync matcher inline and wraps Groq calls in
  `asyncio.to_thread`. The sync CSV-only path stays unchanged.
- **`CompetitorProduct.price` is optional** (`Decimal | None`): a fetched offer may have no price
  until a PDP is read. The writer guards all landed-price math for `None`.
- **Groq model** defaults to `llama-3.3-70b-versatile` (set via `GROQ_MODEL`).
- **Playwright is an optional extra** (`[project.optional-dependencies] playwright`), lazily
  imported only when `PDP_PLAYWRIGHT_ENABLED=true`; httpx is the default fetcher.
- **Price parsing**: feed/scrape strings (locale-formatted) use `normalize_price(value, currency,
  market)`; JSON-LD / AI / API values (machine-formatted) use `parse_price_loose` so `789.0`
  stays `789.0` rather than being read as `7890` under IT thousands-separator rules.
